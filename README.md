# antiCTS | 腕管综合征辅助缓解程序

## 简介

- CTS，即腕管综合征（Carpal tunnel syndrome），俗称“鼠标手”，由于长时间使用鼠标引起~~牛马们懂的都懂~~
- 为避免或减轻已有的CTS，最好的方式是不使用普通鼠标，而是使用更贴合人体工学的轻型鼠标（高端游戏鼠）/人体工学鼠标或轨迹球，以及手柄
- ~~高端游戏鼠标大部分不支持蓝牙，其他几项实在是太tm贵了，如果能接受为了工作单独买个大几百的人体工学鼠标那请随意~~
- 同时由于作者所在公司监管严格，无法随意使用可执行文件，但允许在开发环境下执行代码，故有此项目诞生
- 本项目旨在为处于公司监管环境下的牛马们提供纯代码方式将手柄映射为鼠标的方案

## 特性

- 纯代码运行，仅需三个额外python库（pygame与pynput）
- 如果能确定自己手柄是哪种协议，那么就可以只选择对应的库
    - Xinput：XInput-Python
    - Dinput：pygame
- 可傻瓜式一键执行，也可通过config文件快速配置
- 支持各位调节代码以适应自己的手柄
- 目前仅在Windows上做了测试，Linux桌面环境可能出现不可预知的结果

## 安装与使用

### 使用方法（傻瓜式）

请直接从release下载软件源码包（.zip），想办法转移到工作电脑上，解压后双击run.bat文件即可。

注：正常情况会弹出一个黑色窗口，请将其最小化后继续工作。

### 外网环境手动安装

1. 安装python
   下载并安装 Python：
   - [Python 官方下载页面](https://www.python.org/downloads/)
   - [Python 阿里云镜像下载页面](https://mirrors.aliyun.com/python-release/)

2. 克隆/依赖/执行：
   ```bash
   git clone https://github.com/Xinweigh/anticts.git
   pip install -r requirements.txt
   python anticts.py
   ```

## 贡献

欢迎贡献代码！目前仅有两个分支，请使用dev分支开发，master分支仅作为通过测试的内容提供

以下是一个典型步骤：

1. Fork 仓库
2. 创建分支：`git checkout -b dev`
3. 提交更改：`git commit -m "描述更改内容"`
4. 推送分支：`git push origin dev`
5. 提交 Pull Request

## 许可证

考虑到本项目的初衷，故采用 [GPLv3 License](LICENSE) 进行开源。

## 联系方式

如有问题，请联系：[your-email@example.com](mailto:your-email@example.com)
