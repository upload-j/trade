# IB Options MCP Server
# This server provides MCP (Model Context Protocol) tools for Interactive Brokers options trading
# It uses the ib_async library to connect to IB and provides tools for getting options chains, quotes, etc.

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Union

from ib_async import IB, Contract, Option, Stock, util
from mcp import Tool
from mcp.server import Server
from mcp.types import TextContent, PromptMessage

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# IB connection settings
IB_HOST = os.getenv('IB_HOST', '127.0.0.1')
IB_PORT = int(os.getenv('IB_PORT', '7497'))
IB_CLIENT_ID = int(os.getenv('IB_CLIENT_ID', '1'))

class IBOptionsMCPServer:
    def __init__(self):
        self.ib = IB()
        self.server = Server("ib-options-mcp-server")
        self._setup_tools()

    def _setup_tools(self):
        @self.server.list_tools()
        async def handle_list_tools() -> List[Tool]:
            return [
                Tool(
                    name="get_options_chain",
                    description="Get options chain for a given underlying symbol",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string", "description": "Underlying symbol (e.g., 'AAPL')"},
                            "exchange": {"type": "string", "description": "Exchange (e.g., 'SMART')", "default": "SMART"},
                            "currency": {"type": "string", "description": "Currency (e.g., 'USD')", "default": "USD"},
                            "include_expired": {"type": "boolean", "description": "Include expired options", "default": False}
                        },
                        "required": ["symbol"]
                    }
                ),
                Tool(
                    name="get_option_quote",
                    description="Get quote for a specific option contract",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string", "description": "Underlying symbol"},
                            "strike": {"type": "number", "description": "Strike price"},
                            "expiration": {"type": "string", "description": "Expiration date (YYYYMMDD)"},
                            "right": {"type": "string", "description": "Option type ('C' for call, 'P' for put)"},
                            "exchange": {"type": "string", "description": "Exchange", "default": "SMART"},
                            "currency": {"type": "string", "description": "Currency", "default": "USD"}
                        },
                        "required": ["symbol", "strike", "expiration", "right"]
                    }
                ),
                Tool(
                    name="get_stock_quote",
                    description="Get quote for a stock",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string", "description": "Stock symbol"},
                            "exchange": {"type": "string", "description": "Exchange", "default": "SMART"},
                            "currency": {"type": "string", "description": "Currency", "default": "USD"}
                        },
                        "required": ["symbol"]
                    }
                )
            ]

        @self.server.call_tool()
        async def handle_call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
            if name == "get_options_chain":
                return await self._get_options_chain(arguments)
            elif name == "get_option_quote":
                return await self._get_option_quote(arguments)
            elif name == "get_stock_quote":
                return await self._get_stock_quote(arguments)
            else:
                raise ValueError(f"Unknown tool: {name}")

    async def _get_options_chain(self, args: Dict[str, Any]) -> List[TextContent]:
        symbol = args["symbol"]
        exchange = args.get("exchange", "SMART")
        currency = args.get("currency", "USD")
        include_expired = args.get("include_expired", False)

        try:
            # Connect to IB if not already connected
            if not self.ib.isConnected():
                await self.ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)

            # Create stock contract
            stock = Stock(symbol, exchange, currency)
            await self.ib.qualifyContractsAsync(stock)

            # Get options chain
            chains = await self.ib.reqSecDefOptParamsAsync(stock.symbol, "", stock.secType, stock.conId)

            if not chains:
                return [TextContent(type="text", text=f"No options chains found for {symbol}")]

            # Get the first chain (usually the most liquid)
            chain = chains[0]
            expirations = chain.expirations
            strikes = chain.strikes

            # Filter expired if requested
            if not include_expired:
                today = datetime.now().strftime("%Y%m%d")
                expirations = [exp for exp in expirations if exp >= today]

            result = {
                "symbol": symbol,
                "expirations": expirations,
                "strikes": strikes,
                "exchange": chain.exchange,
                "underlyingConId": chain.underlyingConId,
                "tradingClass": chain.tradingClass,
                "multiplier": chain.multiplier
            }

            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        except Exception as e:
            logger.error(f"Error getting options chain for {symbol}: {e}")
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    async def _get_option_quote(self, args: Dict[str, Any]) -> List[TextContent]:
        symbol = args["symbol"]
        strike = args["strike"]
        expiration = args["expiration"]
        right = args["right"]
        exchange = args.get("exchange", "SMART")
        currency = args.get("currency", "USD")

        try:
            # Connect to IB if not already connected
            if not self.ib.isConnected():
                await self.ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)

            # Create option contract
            option = Option(symbol, expiration, strike, right, exchange, currency)
            await self.ib.qualifyContractsAsync(option)

            # Get market data
            ticker = self.ib.reqMktData(option, "", False, False)

            # Wait for data
            await asyncio.sleep(2)

            if ticker.last != ticker.last:  # Check if last is NaN
                return [TextContent(type="text", text=f"No quote available for {symbol} {expiration} {strike} {right}")]

            result = {
                "symbol": symbol,
                "expiration": expiration,
                "strike": strike,
                "right": right,
                "bid": ticker.bid,
                "ask": ticker.ask,
                "last": ticker.last,
                "volume": ticker.volume,
                "open": ticker.open,
                "high": ticker.high,
                "low": ticker.low,
                "close": ticker.close
            }

            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        except Exception as e:
            logger.error(f"Error getting option quote for {symbol} {expiration} {strike} {right}: {e}")
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    async def _get_stock_quote(self, args: Dict[str, Any]) -> List[TextContent]:
        symbol = args["symbol"]
        exchange = args.get("exchange", "SMART")
        currency = args.get("currency", "USD")

        try:
            # Connect to IB if not already connected
            if not self.ib.isConnected():
                await self.ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)

            # Create stock contract
            stock = Stock(symbol, exchange, currency)
            await self.ib.qualifyContractsAsync(stock)

            # Get market data
            ticker = self.ib.reqMktData(stock, "", False, False)

            # Wait for data
            await asyncio.sleep(2)

            if ticker.last != ticker.last:  # Check if last is NaN
                return [TextContent(type="text", text=f"No quote available for {symbol}")]

            result = {
                "symbol": symbol,
                "bid": ticker.bid,
                "ask": ticker.ask,
                "last": ticker.last,
                "volume": ticker.volume,
                "open": ticker.open,
                "high": ticker.high,
                "low": ticker.low,
                "close": ticker.close
            }

            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        except Exception as e:
            logger.error(f"Error getting stock quote for {symbol}: {e}")
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    async def run(self):
        # Start the MCP server
        from mcp.server.stdio import stdio_server

        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                self.server.create_initialization_options()
            )

async def main():
    server = IBOptionsMCPServer()
    await server.run()

if __name__ == "__main__":
    asyncio.run(main())
