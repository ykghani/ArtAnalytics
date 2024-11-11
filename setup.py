from setuptools import setup, find_packages

setup(
    name="art-analytics",
    version="0.1",
    packages=find_packages(),
    install_requires=[
        'pandas',
        'numpy',
        'tqdm',
        'requests',
        'pillow',
        'requests-cache'
    ],
)