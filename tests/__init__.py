"""gha-runner 单元测试包。

跑法（在 skill 根目录）：
    PYTHONPATH=. python3 -m unittest discover -s tests -t . -v

这些测试是纯本地的，不碰网络、不需要 gh 认证，可以在 ubuntu / macos / windows
三种 runner 上直接跑 —— 阶段 B 的三平台 CI 就是这么验的。
"""
