# kstone-viewer

[English](README.md) | **简体中文**

[kstone](https://github.com/AgilexOpensource/kstone) 调试服务的浏览器 viewer，用于冒烟测试。它通过 TCP 连接服务，不依赖 `kstone` 包，可以和机器人不在同一台机器上。

## 安装

```bash
pip install "git+https://github.com/AgilexOpensource/kstone_viewer.git"
```

依赖 viser、yourdfpy、trimesh 和 numpy。

## 使用

先启动调试服务：`kstone run <配置> --realtime --serve 7311`，或在 Python 中调用 `engine.serve()`，或在 C++ 中创建 `DebugServer`。然后：

```bash
kstone-viewer --connect 127.0.0.1:7311
```

viewer 会打印要打开的地址，默认 `http://127.0.0.1:8080`。服务会发送 URDF 及其网格文件；`--urdf` 可改为渲染本地文件。

连接远程服务时，用 `--token-file` 或 `$KSTONE_TOKEN` 提供 token。协议不加密，跨不可信网络时用 SSH 转发端口，再连本地端口：

```bash
ssh -N -L 7311:127.0.0.1:7311 user@robot
kstone-viewer --connect 127.0.0.1:7311
```

其他选项见 `kstone-viewer --help`。

## 测试

```bash
python tests/test_viewer.py
# 已安装 kstone，且 kstone 仓库和本仓库放在同一目录下
python tests/test_protocol.py ../kstone/tests/fixtures
```

`test_viewer.py` 不需要浏览器和 kstone。`test_protocol.py` 启动 kstone 调试服务，再用 viewer 的客户端去连接。
