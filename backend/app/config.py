import os

DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql+psycopg://marketdesk:marketdesk_local_dev@localhost:5432/marketdesk')
MODEL = os.getenv('NVIDIA_MODEL', 'nvidia/nemotron-3-ultra-550b-a55b')
API_KEY = os.getenv('NVIDIA_API_KEY', '')
BASE_URL = 'https://integrate.api.nvidia.com/v1'
US_SYMBOLS = ['NVDA', 'AAPL', 'MSFT', 'AMZN', 'GOOGL', 'META', 'TSLA', 'AMD']
NSE_SYMBOLS = ['RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'ICICIBANK.NS', 'ITC.NS', 'LT.NS', 'SBIN.NS']
DEFAULT_SYMBOLS = NSE_SYMBOLS
