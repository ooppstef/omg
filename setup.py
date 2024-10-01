from setuptools import setup, find_packages

setup(
    name="charles_stock_tools",
    version="0.1",
    packages=find_packages(),
    py_modules=[
        "main_controller",
    ],
    entry_points={
        "console_scripts": [
            "omg=main_controller:main",
        ],
    },
    install_requires=["requests", "adata", "pypinyin"],
    include_package_data=True,
)
