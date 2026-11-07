"""Start Compose with a key held only in process/container environment."""
import getpass
import os
from pathlib import Path
import subprocess
import sys

key = sys.stdin.readline().strip() if '--key-stdin' in sys.argv else getpass.getpass('NVIDIA API key (blank disables chat): ').strip()
env = os.environ.copy()
env['NVIDIA_API_KEY'] = key
desktop_compose = Path(r'C:\Program Files\Docker\Docker\resources\cli-plugins\docker-compose.exe')
command = [str(desktop_compose)] if os.name == 'nt' and desktop_compose.exists() else ['docker', 'compose']
result = subprocess.run([*command, 'up', '--build', '-d'], cwd=Path(__file__).resolve().parents[1], env=env)
sys.exit(result.returncode)
