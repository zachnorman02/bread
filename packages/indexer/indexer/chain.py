import asyncio, json, traceback
import os
from dataclasses import dataclass, field
from typing import Dict, List, Tuple
from aiohttp import ClientResponse, ClientSession

from indexer.exceptions import APIResponseError

ChainApiResponse = Tuple[str | None, dict | None]
LATEST = "latest"


@dataclass
class CosmosChain:
    chain_id: str
    blocks_endpoint: str
    txs_endpoint: str
    apis: Dict[str, Dict[str, int]]
    current_api_index: int = 0
    time_between_blocks: int = 1

    async def is_valid_response(self, resp: ClientResponse) -> bool:
        """Check if the response is in the correct format

        Args:
            resp (ClientResponse): raw response from the api call to check

        Returns:
            bool: True if the response is valid, False otherwise
        """
        # could we return specific error messages here to save to db?
        try:
            return (
                list((await self.get_json(resp)).keys())
                != ["code", "message", "details"]
                and resp.status == 200
            )
        except Exception as e:
            return False

    async def get_json(self, resp: ClientResponse) -> dict:
        return json.loads(await resp.read())

    def get_next_api(self):
        return list(self.apis.keys())[self.current_api_index]

    def add_api_miss(self, api: str):
        index = list(self.apis.keys()).index(api)
        list(self.apis.values())[index]["miss"] += 1

    def add_api_hit(self, api: str):
        index = list(self.apis.keys()).index(api)
        list(self.apis.values())[index]["hit"] += 1

    def iterate_api(self):
        self.current_api_index = (self.current_api_index + 1) % len(self.apis)

    def remove_api(self, api: str):
        """Remove an api from the list of apis

        Args:
            api (str): api to remove from the list of apis
        """
        try:
            api_index = self.apis.pop(api)
        except KeyError:
            # if api is not in list of apis, do nothing
            pass

    async def _get(
        self,
        endpoint: str,
        session: ClientSession,
        max_retries: int,
    ) -> ChainApiResponse:
        """Get data from an endpoint with retries

        Args:
            endpoint (str): endpoint to
            session (aiohttp.ClientSession): _description_
            sem (asyncio.Semaphore): _description_
            max_retries (int): _description_

        Raises:
            APIResponseError: _description_

        Returns:
            Tuple[str, dict] | None: str is the api that was hit, dict is the response if valid, otherwise None
        """
        retries = 0

        while retries < max_retries:
            cur_api = self.get_next_api()
            try:
                async with session.get(f"{cur_api}{endpoint}") as resp:
                    if await self.is_valid_response(resp):
                        self.add_api_hit(cur_api)
                        return cur_api, await self.get_json(resp)
                    else:
                        raise APIResponseError("API Response Not Valid")
            except APIResponseError as e:
                print(f"failed to get {cur_api}{endpoint}")

                self.add_api_miss(cur_api)
                self.iterate_api()

            retries += 1
        return None, None

    async def get_block(
        self,
        session: ClientSession,
        height: int | str = LATEST,
        max_retries=5,
    ) -> dict | None:
        """Get block data from an api

        Args:
            session (aiohttp.ClientSession): ClientSession to use for the request
            sem (asyncio.Semaphore): Semaphore to use for the request
            height (str, optional): height of the block to get. Defaults to "latest".
            max_retries (int, optional): max number of retries to make. Defaults to 5.

        Returns:
            dict | None: block data if valid, otherwise None
        """
        api_res = await self._get(
            self.blocks_endpoint.format(height), session, max_retries
        )
        return self.verify_and_return_block_data(api_res)

    def verify_and_return_block_data(self, api_res: ChainApiResponse) -> dict | None:
        """Verify that the block data is from the correct chain

        Args:
            api_res (ChainApiResponse): api that was hit and the response data

        Returns:
            dict | None: block data if valid, otherwise None
        """
        api, data = api_res
        if data is None or api is None:
            return None

        if self.chain_id == data["block"]["header"]["chain_id"]:
            return data

        self.remove_api(api)
        return None

    async def get_block_txs(
        self,
        session: ClientSession,
        height: int | str,
        max_retries=5,
    ) -> dict | None:
        """Get transactions from a block

        Args:
            session (aiohttp.ClientSession): ClientSession to use for the request
            sem (asyncio.Semaphore): Semaphore to use for the request
            height (str): height of the block to get transactions from
            max_retries (int, optional): max number of retries to make. Defaults to 5.

        Returns:
            dict | None: transactions if valid, otherwise None
        """
        api, data = await self._get(
            self.txs_endpoint.format(height), session, max_retries
        )
        return data

    async def get_lowest_height(self, session: ClientSession):
        block_res = await session.get(
            self.get_next_api() + self.blocks_endpoint.format(1)
        )
        block_res_json = await block_res.json()
        lowest_height = (
            block_res_json["message"].split("height is ")[-1].split(":")[0].lstrip()
        )
        return int(lowest_height)


async def get_chain_registry_info(
    session: ClientSession, chain_registry_name: str
) -> Tuple[str, List[str]]:
    """Pull Chain Info from Cosmos Chain Registry

    Args:
        session (ClientSession): Aiohttp Session to query from
        chain_registry_name (str): Name of chain in Cosmos Chain Registry

    Returns:
        Tuple[str, List[str]]: Chain_id, list of apis
    """
    raw_chain = await session.get(
        url=f"https://raw.githubusercontent.com/cosmos/chain-registry/master/{chain_registry_name}/chain.json"
    )
    raw_chain = json.loads(await raw_chain.read())
    apis: List[str] = [api["address"] for api in raw_chain["apis"]["rest"]]
    chain_id: str = raw_chain["chain_id"]
    return chain_id, apis


async def get_chain_info(session) -> Tuple[str, Dict[str, dict]]:
    chain_registry_name = os.getenv("CHAIN_REGISTRY_NAME", None)
    load_external_apis = os.getenv("LOAD_CHAIN_REGISTRY_APIS", True)
    apis = []
    if chain_registry_name:
        chain_id, chain_registry_apis = await get_chain_registry_info(
            session, chain_registry_name
        )
        if load_external_apis:
            apis.extend(chain_registry_apis)
    else:
        raise EnvironmentError(
            "CHAIN_REGISTRY_NAME environment variable not provided. This is needed to load the correct chain_id"
        )
    env_apis = os.getenv("APIS", "").split(",")

    apis.extend(env_apis)
    print(apis)
    formatted_apis = {api: {"hit": 0, "miss": 0} for api in apis}
    return chain_id, formatted_apis


async def get_chain_from_environment(session: ClientSession) -> CosmosChain:
    """Creates a CosmosChain based on environment config

    future note: I did not override __init__ due to potential other use cases for CosmosChain

    Args:
        session (ClientSession): session to query external requests from

    Raises:
        EnvironmentError: Error if environment variables are not defined

    Returns:
        CosmosChain: CosmosChain object configured from current environment
    """
    chain_id, apis = await get_chain_info(session)
    time_between = os.getenv("TIME_BETWEEN_BLOCKS", "1")
    try:
        time_between = int(time_between)
    except:
        raise EnvironmentError("TIME_BETWEEN_BLOCKS is not of type int")
    chain = CosmosChain(
        chain_id=chain_id,
        blocks_endpoint=os.getenv(
            "BLOCKS_ENDPOINT", "/cosmos/base/tendermint/v1beta1/blocks/{}"
        ),
        txs_endpoint=os.getenv(
            "TXS_ENDPOINT", "/cosmos/tx/v1beta1/txs?events=tx.height={}"
        ),
        apis=apis,
        time_between_blocks=time_between,
    )
    return chain
