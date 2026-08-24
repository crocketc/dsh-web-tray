# AGENTS.md — 本仓库的 Agent 工作约定

## 提交（硬性）

- commit 信息只用 **ASCII 单行**，模板：`git -c commit.gpgsign=false commit -q -m 'feat(scope): ticket NN summary'`。
  本仓库运行环境的部分 shell 对含中文/多行的 `-m` 参数会挂死（已致一个子代理卡死在提交步骤）。
- 阶段性成果每绿一批就提交一次，不要攒到最后一把梭；排障临时脚本用完即删，绝不 `git add`。

## 测试与静态检查

- 全量：`.venv/bin/python -m unittest discover -s tests -q`（cwd=仓库根或 worktree 根）
- 静态门槛：`.venv/bin/python -m ruff check <改动文件> --select F,E9`（新代码零新增；存量 13 个不在治理范围）
- 网络与子进程在单测中一律 mock；集成/实机验证走 `tests/test_integration_live.py` 风格的手动清单

## 多 Agent 协作（git worktree 模式）

- 每票一个 worktree（`.worktrees/wt-NN`，分支 `wt/NN-slug`），子代理只准操作自己的 worktree
- 主 Agent 按票 `merge --no-ff` 归集（保留票边界），每次合并后立即跑全量测试
- 子代理只开发+提交，不 push / 不 merge / 不评审；死锁特征（连续多轮 worktree 状态零变化）出现时主 Agent 立即介入接管

## 冒烟

- CLI 冒烟用临时目录隔离真实实例：`DSH_WEB_TRAY_HOME=$(mktemp -d)` 后拷贝 config.json 再跑
- 绝不在 macOS 上启动托盘 GUI 主循环（pystray 会卡死无头会话）
