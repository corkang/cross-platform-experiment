import os
import sys
import subprocess

target = sys.argv[1]  # e.g. cookiecutter/cookiecutter
safe_name = target.replace("/", "__")
repo_dir = os.path.join("targets", safe_name)

os.makedirs("targets", exist_ok=True)

if not os.path.exists(repo_dir):
    subprocess.run(
        ["git", "clone", "--depth", "1", f"https://github.com/{target}.git", repo_dir],
        check=True
    )

print(repo_dir)