import os
import subprocess
import shutil
import sys

def build():
    print("Building backend executable with Nuitka...")
    
    # Path to python executable
    python_exe = r"C:\Users\User\AppData\Local\Programs\Python\Python310\python.exe"
    
    # Entry point
    entry_point = "server.py"
    
    # Use Nuitka to compile the server.py
    cmd = [
        python_exe, "-m", "nuitka",
        "--standalone",
        "--include-package=playwright",
        "--include-package=playwright_stealth",
        "--include-package=curl_cffi",
        "--include-package=uvicorn",
        "--include-package=fastapi",
        "--windows-console-mode=disable", # Updated flag from warning
        "--output-dir=build",
        "server.py"
    ]
    
    # Run Nuitka
    try:
        if os.path.exists("build"):
            shutil.rmtree("build")
        subprocess.check_call(cmd)
        print("Backend build successful!")
    except subprocess.CalledProcessError as e:
        print(f"Backend build failed with error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    build()
