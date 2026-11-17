"""Double-click launcher: reuse the local workbench and open verified Job Search."""
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen
from runtime_config import open_jobsearch_workbench
ROOT=Path(__file__).resolve().parents[1]

def running_info():
    try:
        with urlopen('http://127.0.0.1:8765/api/jobs',timeout=5) as response:
            data=json.load(response)
        return data if isinstance(data,dict) and isinstance(data.get('jobs'),list) and 'ready_count' in data else None
    except (OSError,ValueError):return None

def healthy():
    return running_info() is not None

def stop_old_server():
    """Stop a hidden workbench server started from older code (it keeps running after the page is closed)."""
    command=("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -like '*manual_workbench.py*' } | "
             "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    subprocess.run(['powershell','-NoProfile','-Command',command],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=30)
    for _ in range(20):
        if not healthy():return
        time.sleep(.5)
    raise RuntimeError('旧的工作台没有停止，请重启电脑后再打开')

def main():
    from manual_workbench import code_version
    info=running_info()
    if info is not None and info.get('code_version')!=code_version(ROOT):
        stop_old_server()  # code changed since it started: restart so the page shows the new version
        info=None
    if info is None:
        logs=ROOT/'tmp';logs.mkdir(exist_ok=True)
        # pythonw is used only for the launcher; server gets a console interpreter and file logs.
        python=Path(sys.executable).with_name('python.exe')
        with (logs/'workbench-launch.log').open('a',encoding='utf-8') as log:
            child=subprocess.Popen([str(python),str(ROOT/'scripts/manual_workbench.py')],cwd=ROOT,
                stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        for _ in range(30):
            if healthy():break
            if child.poll() is not None:raise RuntimeError('工作台未启动，请查看 tmp/workbench-launch.log')
            time.sleep(.5)
        else:raise RuntimeError('工作台启动超时，请查看 tmp/workbench-launch.log')
    open_jobsearch_workbench()

if __name__=='__main__':
    try:main()
    except Exception as exc:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None,str(exc),'求职工作台启动失败',0x10)
        sys.exit(1)
