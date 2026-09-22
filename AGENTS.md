# Goofish-Z project boundary

This repository owns the Xianyu workbench, search and item tools, monitoring,
and their shared CLI, MCP and HTTP interfaces.

Cross-market price comparison is the separate PriceRadar project:
- Local source: the independent `PriceRadar` project directory
- Repository: https://github.com/E-R-Butch/PriceRadar

A request to continue Goofish-Z does not authorize continuing PriceRadar.
Do not add Taobao, JD, Pinduoduo collection or cross-market comparison here.
The sibling `Goofish-Z-prices` worktree and `feat/readonly-market-prices`
branch are historical pre-split work, not the current Goofish-Z roadmap.
PriceRadar may consume Goofish-Z through optional loopback HTTP calls;
keep the projects' packages, data directories and services independent.

Follow the parent Secondhand-Sale instructions for private data and runtime
ownership. Only generic tool code belongs in this public repository.
