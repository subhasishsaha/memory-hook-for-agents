from setuptools import setup, find_packages

setup(
    name="memory-hook",
    version="0.1.0",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "memory-hook=memory_hook.cli:main",
        ],
    },
    python_requires=">=3.8",
)
