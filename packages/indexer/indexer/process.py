import asyncio
import aiohttp
import asyncpg
from indexer.chain import CosmosChain
from indexer.db import Database, upsert_raw_blocks, upsert_raw_txs
from indexer.exceptions import ChainDataIsNoneError, ChainIdMismatchError


async def process_block(
    height: int,
    chain: CosmosChain,
    db: Database,
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    block_data: dict | None = None,
) -> bool:
    if block_data is None:
        # this is because block data might be passed in from the live chain data (so removing a duplicated request)
        block_data = await chain.get_block(session, sem, str(height))
    try:
        if block_data is not None:
            await upsert_raw_blocks(db, block_data)
        else:
            raise ChainDataIsNoneError(f"block_data is None - {block_data}")
        return True
    except ChainDataIsNoneError as e:
        print(f"upsert_block error {repr(e)} - {height}")
        return False
    except Exception as e:
        raise e


async def process_tx(
    height: int,
    chain: CosmosChain,
    db: Database,
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
) -> bool:
    txs_data = await chain.get_block_txs(session, sem, str(height))
    try:
        if txs_data is None:
            raise ChainDataIsNoneError("txs_data is None")

        txs_data = txs_data["tx_responses"]
        await upsert_raw_txs(db, {str(height): txs_data}, chain.chain_id)
        return True

    except ChainDataIsNoneError as e:
        print(f"upsert_txs error {repr(e)} - {height}")
        return False
    except KeyError as e:
        print("tx_response key doesn't exist")
        return False
    except Exception as e:
        raise e
