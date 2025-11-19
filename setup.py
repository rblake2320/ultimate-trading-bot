"""
Setup configuration for Ultimate Trading Bot
"""

from setuptools import setup, find_packages
from pathlib import Path

# Read README for long description
readme_file = Path(__file__).parent / "README.md"
long_description = readme_file.read_text(encoding="utf-8") if readme_file.exists() else ""

# Read requirements
requirements_file = Path(__file__).parent / "requirements.txt"
requirements = []
if requirements_file.exists():
    with open(requirements_file, "r", encoding="utf-8") as f:
        requirements = [
            line.strip()
            for line in f
            if line.strip()
            and not line.startswith("#")
            and not line.startswith("-")
        ]

setup(
    name="ultimate-trading-bot",
    version="0.1.0",
    author="rblake2320",
    author_email="rblake2320@aol.com",
    description="AI-Powered Cryptocurrency Trading Bot with advanced risk management",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/rblake2320/ultimate-trading-bot",
    project_urls={
        "Bug Tracker": "https://github.com/rblake2320/ultimate-trading-bot/issues",
        "Documentation": "https://github.com/rblake2320/ultimate-trading-bot/wiki",
        "Source Code": "https://github.com/rblake2320/ultimate-trading-bot",
    },
    packages=find_packages(exclude=["tests", "tests.*", "docs", "examples"]),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Financial and Insurance Industry",
        "Intended Audience :: Developers",
        "Topic :: Office/Business :: Financial :: Investment",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Operating System :: OS Independent",
        "Environment :: Console",
        "Natural Language :: English",
    ],
    python_requires=">=3.9",
    install_requires=requirements,
    extras_require={
        "dev": [
            "pytest>=7.4.0",
            "pytest-asyncio>=0.21.0",
            "pytest-cov>=4.1.0",
            "black>=23.7.0",
            "flake8>=6.0.0",
            "mypy>=1.5.0",
            "isort>=5.12.0",
        ],
        "ml": [
            "tensorflow>=2.13.0",
            "torch>=2.0.0",
            "scikit-learn>=1.3.0",
        ],
        "viz": [
            "matplotlib>=3.7.0",
            "plotly>=5.15.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "trading-bot=main:main",
        ],
    },
    include_package_data=True,
    package_data={
        "": ["*.json", "*.yaml", "*.yml", "*.md"],
    },
    keywords=[
        "trading",
        "cryptocurrency",
        "bitcoin",
        "ethereum",
        "trading-bot",
        "algorithmic-trading",
        "crypto-trading",
        "binance",
        "ccxt",
    ],
    license="MIT",
    zip_safe=False,
)
