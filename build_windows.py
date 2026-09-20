"""Build with a minimal DLL search path so unrelated installed tools cannot leak in."""
import argparse
import os
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--distpath', default='dist')
parser.add_argument('--workpath', default='build')
parser.add_argument('--specpath', default='build')
args = parser.parse_args()
root = Path(__file__).resolve().parent
os.environ['PATH'] = os.pathsep.join([
    str(Path(sys.executable).parent), sys.base_prefix,
    str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32'),
    os.environ.get('SystemRoot', r'C:\Windows'),
])
from PyInstaller.__main__ import run
run(['--noconfirm', '--clean', '--windowed', '--onedir', '--name', 'MPC Stem Exporter',
     '--distpath', str(Path(args.distpath).resolve()),
     '--workpath', str(Path(args.workpath).resolve()),
     '--specpath', str(Path(args.specpath).resolve()),
     '--hidden-import', 'mido.backends.rtmidi', '--hidden-import', 'rtmidi',
     str(root / 'launch.py')])
