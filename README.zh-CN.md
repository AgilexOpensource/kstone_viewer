# kstone-viewer

[English](README.md)

[kstone](https://github.com/AgilexOpensource/kstone) 调试服务的浏览器 viewer，用于在桌面浏览器中对运行中的引擎做冒烟测试。

它通过 TCP 连接服务，不依赖 `kstone` 包，可以和机器人不在同一台机器上。页面提供运行控制、模块端口与调参、曲线、安全统计、关节和显示设置。

## 环境要求

Python 3.10 及以上。已在 Ubuntu 22.04 上测试。

## 安装

```bash
pip3 install "git+https://github.com/AgilexOpensource/kstone_viewer.git"
```

或者，推荐使用虚拟环境，避免改动系统 Python：

```bash
python3 -m venv ~/.venvs/kstone-viewer
source ~/.venvs/kstone-viewer/bin/activate
pip install "git+https://github.com/AgilexOpensource/kstone_viewer.git"
```

每个新终端运行 `kstone-viewer` 前先激活虚拟环境。依赖（viser、yourdfpy、trimesh、numpy）会自动安装。

## 使用

先用以下任一方式启动调试服务：

- 命令行：`kstone run <配置> --serve 7311`
- Python：`engine.serve()`
- C++：`kstone::DebugServer::start(engine, options)`

服务默认监听 `127.0.0.1:7311`。然后连接 viewer：

```bash
kstone-viewer --connect 127.0.0.1:7311
```

viewer 会打印 `connected to 127.0.0.1:7311; open http://127.0.0.1:8080`，在浏览器中打开该地址。服务会发送 URDF 及其网格文件，缓存在 `$XDG_CACHE_HOME/kstone/models`；`--urdf` 可改为渲染本地文件。服务断开后 viewer 会持续重连。按 `Ctrl+C` 停止。

## 选项

| 选项 | 默认值 | 说明 |
|---|---|---|
| `--connect` | `127.0.0.1:7311` | 调试服务地址，`host:port` |
| `--host` | `127.0.0.1` | viewer 网页地址 |
| `--port` | `8080` | viewer 网页端口 |
| `--urdf` | 服务提供的模型 | 要渲染的本地 URDF |
| `--token-file` | `$KSTONE_TOKEN` | 保存服务 token 的文件 |
| `--rate` | 渲染频率，不超过发布频率 | 状态频率（Hz） |
| `--prefs` | `$XDG_CONFIG_HOME/kstone/viewer.json` | 偏好设置文件 |
| `--no-prefs` | 关闭 | 不读写偏好设置 |

- `$XDG_CACHE_HOME` 和 `$XDG_CONFIG_HOME` 未设置时分别为 `~/.cache` 和 `~/.config`。
- 偏好设置文件保存显示设置和页面语言。

## 远程使用与安全

服务绑定非回环地址时需要 token。viewer 使用同一个 token：

```bash
kstone-viewer --connect <机器人IP>:7311 --token-file ~/.kstone-token
```

也可以在环境中设置 `KSTONE_TOKEN`，代替 `--token-file`。

协议不加密，token 以明文发送。跨不可信网络时，让服务保持在回环地址，并用 SSH 转发端口：

```bash
ssh -N -L 7311:127.0.0.1:7311 user@<机器人IP>
kstone-viewer --connect 127.0.0.1:7311
```

viewer 页面没有登录。`--host` 保持 `127.0.0.1`，其他机器通过 SSH 访问页面，不要绑定到网络地址：

```bash
ssh -N -L 8080:127.0.0.1:8080 user@<viewer主机>
```

- 同一服务的客户端之间平等，最后写入的生效。打开页面的所有浏览器都算同一个 viewer。
- 你的代码写入的运动目标（frame 和关节目标、底盘运动）保持优先。占用期间 viewer 对它们的写入会被拒绝（模块上显示 API 占用），并且不会重放。

## 常见问题

- `[Errno 111] Connection refused`：`--connect` 地址上没有服务在监听。启动服务并检查地址和端口。
- `--connect expects host:port`：地址需写成 `host:port`，例如 `127.0.0.1:7311`。
- `server requires a token`：服务启用了 token。用 `--token-file` 或 `$KSTONE_TOKEN` 提供。
- `authentication failed: bad token`：token 与服务端不一致。两端使用同一个文件或值。
- `server refused the connection: server full (8 clients)`：关闭该服务的其他客户端。
- `unsupported greeting`：服务的协议版本不同，或不是 kstone 调试服务。使用相互匹配的 kstone 和 kstone-viewer 版本。
- `no URDF: the server serves no model assets and reports no local file; pass --urdf`：用 `--urdf` 指定本地 URDF。
- `warning: the server lacks model files, they will not render`：服务端机器上缺少 URDF 引用的所列文件。在服务端补齐这些文件。
- `kstone_viewer needs <module>: reinstall kstone-viewer`：缺少依赖。在当前环境中重新安装本包。
- `preferences ignored`：偏好设置文件无法读取。修复或删除该文件，或使用 `--no-prefs`。

## 开发

```bash
git clone https://github.com/AgilexOpensource/kstone_viewer.git && cd kstone_viewer
pip install -e .
python tests/test_viewer.py
# 需要已安装 kstone，且 kstone 仓库和本仓库放在同一目录下
python tests/test_protocol.py ../kstone/tests/fixtures
```

`test_viewer.py` 不需要浏览器和 kstone。`test_protocol.py` 启动 kstone 调试服务，再用 viewer 的客户端去驱动它。

## 许可证

Apache-2.0，详见 [LICENSE](LICENSE)。
