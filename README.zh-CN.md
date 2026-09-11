<div align="center">

<img src="docs/images/hero.webp" alt="Jarvis — a floating glass pill that shows what you said and what it's doing" width="820">

# Jarvis

**对着电脑说话。它倾听、回答，并替你打字。**

按住一个键，开口说话，松开——你说的话就出现在当前应用的
光标处。也可以开启一段真正*能做事*的语音对话：
搜索网页、打开你的应用、在你的电脑上执行任务。

无需订阅，用你自己的 OpenAI 密钥。**每分钟低至 $0.0045。**

[![许可证：MIT](https://img.shields.io/badge/License-MIT-3b82f6.svg)](LICENSE)
[![CI: passing](https://github.com/RizN91/jarvis/actions/workflows/tests.yml/badge.svg)](https://github.com/RizN91/jarvis/actions/workflows/tests.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3b82f6.svg)](pyproject.toml)
[![平台：Windows 10/11](https://img.shields.io/badge/platform-Windows%2010%2F11-3b82f6.svg)](#平台支持)
[![测试：480 项以上通过](https://img.shields.io/badge/tests-489%20passing-22c55e.svg)](CONTRIBUTING.md)
[![语言：15 种](https://img.shields.io/badge/languages-15-8b5cf6.svg)](#会说你的语言)

**阅读语言：** [English](README.md) · [Español](README.es.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · [Português (BR)](README.pt-BR.md) · **简体中文** · [日本語](README.ja.md)

</div>

---

## 安装

**Windows 10 或 11。** 在 PowerShell 里运行一行命令就完成了整个安装：

```powershell
irm https://raw.githubusercontent.com/RizN91/jarvis/main/install.ps1 | iex
```

它会找到你的 Python，把 Jarvis 克隆到 `%LOCALAPPDATA%\Jarvis\app`，创建虚拟环境，安装锁定版本的依赖，运行一次冒烟检查，并在开始菜单中添加一个快捷方式。

它按用户安装，且可以撤销：绝不提权，绝不替你安装 Python，也绝不修改你的执行策略、Defender 设置或全局 Python。`-DryRun` 会准确显示它将做什么，不做任何更改；`-Uninstall` 可以再次移除它，未经手动输入确认不会碰你的数据文件夹。

首次启动会打开设置向导——麦克风、扬声器、快捷键、唤醒词和预算——并询问你的 OpenAI API 密钥，密钥直接存入 **Windows 凭据管理器**，绝不写入文件。

在你完成向导并开始听写之前，不会向任何地方发送数据，除非是你主动运行的连接测试。

<details>
<summary>或手动安装</summary>

```bat
git clone https://github.com/RizN91/jarvis.git
cd jarvis
setup.cmd
run.cmd
```

或者直接从源码树安装：

```bat
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m jarvis
```

macOS 和 Linux 尚不支持。`install.sh` 会如实说明，而不是假装可以，[平台支持](#平台支持) 解释了原因。

</details>

## 两种启动方式

你完全不用点任何东西。

<table>
<tr>
<td width="50%" valign="top">

**说“Hey Jarvis”**

打开唤醒词，直接开口说话就行。它由一个小型本地模型**在你的电脑上**检测，所以在聆听这句话期间，**不会有任何音频离开你的电脑**。“Hey GPT”也可以，你还能自定义短语。

</td>
<td width="50%" valign="top">

**或者按住一个键**

默认是你的鼠标**侧键**（XButton2）：按住、说话、松开——文字已经出现在光标处。更喜欢键盘？轻按 **F8**。

</td>
</tr>
</table>

| 你按 | 会发生什么 |
| --- | --- |
| 鼠标**侧键**（按住） | 向当前获得焦点的应用听写 |
| `F8` | 开始和停止听写，无需按住不放 |
| `Ctrl` + `Alt` + `Space` | 开启一段语音对话 |
| `Ctrl` + `Alt` + `Pause` | 紧急停止——结束会话和任何正在运行的工具 |
| `Esc` | 取消当前正在发生的操作 |

它们每一个都可以在设置向导中重新绑定——换成别的键、别的鼠标按键，或者不绑定。

## 观看演示

[![Jarvis demo](docs/media/jarvis-preview.webp)](https://cdn.jsdelivr.net/gh/RizN91/jarvis@main/docs/media/jarvis-demo.mp4)

**[▶ 观看 88 秒带声音的演示](https://cdn.jsdelivr.net/gh/RizN91/jarvis@main/docs/media/jarvis-demo.mp4)** · 1080p, 12 MB

---

## 为什么

你想得比打字快。而且你一天到晚都在重复同样的话：同样的邮件、
同样的提交信息、同样的“帮我总结一下这份文档”。

听写工具只解决一半问题，而且要么要订阅、要么像个玩具，要么悄悄
按下回车，把你还没写完的消息发出去。

Jarvis 把这两半都做了，只花几分钱，而且不碍事。它没有
订阅、没有账号、没有遥测，也没有自己的服务器。

<table>
<tr>
<td width="50%">

**说出来，就自动打出来**
> *“Sarah 你好，关于周二的那张发票，能否确认一下采购单号？
> 我今天好把它处理掉。”*

……落到你的邮件、编辑器、工单里，光标在哪就落在哪。

</td>
<td width="50%">

**或者直接开口要**
> *“Jarvis，Vue 3.6 的发布有什么最新消息？”*

……它会先去搜索，再开口回答，而这段对话你随时可以插话。

</td>
</tr>
</table>

## 截图

<div align="center">

<img src="docs/images/pill-states.webp" alt="The Jarvis pill in all six states: idle, dictating, working, listening, speaking, error" width="900">

<sub>悬浮胶囊的六种状态——空闲、听写中、处理中、聆听中、说话中、出错。
它从不抢焦点，无事发生时保持空闲。</sub>

<br><br>

<img src="docs/images/settings-dark.webp" alt="Jarvis setup wizard, dark theme" width="880">

<sub>十二步引导设置：在你真正依赖它之前，先检查麦克风、扬声器、快捷键和密钥。</sub>

<br><br>

<img src="docs/images/settings-light.webp" alt="Jarvis settings, light theme" width="880">

<sub>每个界面都有浅色主题，整个 UI 也都已翻译——见下文。</sub>

</div>

## 费用

你用自己的 OpenAI 密钥，直接向 OpenAI 付费——没有加价、没有订阅、没有
按席位的授权。两个引擎，由你选：

| 引擎 | 模型 | 费用 | 体感 |
| --- | --- | --- | --- |
| **Economy** *（听写默认）* | `gpt-transcribe` | **$0.0045 / 分钟** | 约**每美元 27 小时**；逐字转写最准 |
| **Live** | `gpt-live-1` | **$0.05 / 分钟** | 约每美元 20 分钟；对话式，支持插话和轮次 |

换算一下：**每天听写二十分钟、一周五天，用 Economy 每月大约 45 美分。**
贵的是助手那一半，而且只在你真的和它说话时才计费。

Jarvis 还会统计自己的支出，并在你设定的每日和每月上限处停下。这些上限是
Jarvis 自己的计数器——它读不到你 OpenAI 账户的预算，也从不声称能读。

## 功能

**听写**
- 在**任何**应用里打字——编辑器、浏览器、聊天、终端、工单系统。
- **绝不按回车**，所以不可能把没写完的消息发出去。
- 拒绝密码输入框；在终端里会把文本留给你确认，而不是直接打进 shell。
- 你自己的**词汇表**——人名、行话、产品名——作为提示传给转写引擎，能实实在在地
  提升准确率。
- 撤销不会吃掉你自己打的字。

**语音助手**
- `Ctrl+Alt+Space` 开启对话，支持真正的插话——你一开口它就停。
- 它可以通过工具层搜索网页并在你的电脑上操作，每个操作都需要**你明确说“好”**，
  并会把确切的参数展示出来。
- 可以把长任务交给本地编码智能体（Codex、Claude Code），Jarvis 会说明用了哪一个。

**始终如此**
- **本地唤醒词**——“Hey Jarvis”“Hey GPT”。在你自己的机器上运行
  （`sherpa-onnx`）；**在等待唤醒词时，没有任何音频离开设备**。
- **胶囊悬浮层。** 支持逐像素透明度的悬浮层，显示它听到了什么、正在做什么，
  真正点击穿透、不抢激活。
- **从设计上保护隐私**——没有遥测、没有云端数据库、没有统计分析、没有账号。
  原始音频不保存。
- 浅色与深色主题。支持减少动态效果（reduced motion）。

## 会说你的语言

整个设置向导和设置界面提供 **15 种语言**，并在“设置”里带有切换器：

`English` · `Español` · `Français` · `Deutsch` · `Italiano` · `Nederlands` ·
`Polski` · `Português (BR)` · `Русский` · `Türkçe` · `العربية` (RTL) ·
`हिन्दी` · `中文 (简体)` · `日本語` · `한국어`

首次运行时会从系统检测你的语言。少了某种语言？只要一个文件加一个 pull
request——见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 平台支持

**完整支持 Windows 10 和 11。** 内核构建在 Win32 之上——全局输入钩子、
`SendInput` 文本注入、胶囊用的分层窗口、密钥用的 DPAPI、锁屏/睡眠检测用的
WTS。正是这些让它用起来像原生应用，也正是它暂时无法移植的原因。

**尚不支持 macOS 和 Linux。** 在那些系统上运行会给出清晰的提示并退出，
而不是抛出 traceback。移植已在路线图上；那是一个真正的项目，不是一个开关。

## 路线图

接下来要做的事，大致按顺序：

- [ ] **更多模型，不止 OpenAI**——把 Claude 和 Gemini 作为助手的大脑，并通过
      Ollama 接入本地模型，实现完全离线模式。
- [ ] **更好的语音**和语音选择器，包括你自己的音色。
- [ ] **主题与皮肤**——不止浅色/深色，还有与桌面风格相配的胶囊。
- [ ] **真正的流式听写**——目前 Live 是先录制再回放，所以从松手到出结果的耗时
      包含了你说话的长度。
- [ ] **macOS 移植**（Accessibility API + Quartz）和 **Linux**（X11）。
- [ ] **插件**——让用户可以添加自己的语音命令和工具。
- [ ] **签名安装包**，让 SmartScreen 不再警告。

想法、投票和吐槽都欢迎发到
[Issues](https://github.com/RizN91/jarvis/issues)——路线图跟着大家真正要的东西走。

## 工作原理

简版；详细内容在 [ARCHITECTURE.md](ARCHITECTURE.md)。

- 一个很小的托盘进程负责全局钩子、音频和胶囊。空闲时占用
  **2 个进程、约 53 MB、单个核心的约 0.3%**。
- 设置窗口是**独立**进程，所以 WebView2（约 440 MB）只在你打开它时存在。
- 语音通过 WebSocket 直接发往 OpenAI。**没有 Jarvis 服务器，也没有 localhost
  端口**——没什么可攻击的，也没什么会挂掉。
- 你的数据（历史、词汇表、支出）是你自己磁盘上的 SQLite。不同步到任何地方。

## 安全与隐私

- 你的 API 密钥**只**保存在 Windows 凭据管理器中，由 DPAPI 包装，绝不写入配置
  文件、数据库、日志、命令行或界面。
- 脱敏过滤器会从每一条日志记录中清除形似密钥的字符串。
- **无遥测。无统计分析。无账号。**
- 原始音频不写入磁盘。
- 开机自启是可选项，且可撤销；卸载会删除所有内容。

如何报告漏洞请见 [SECURITY.md](SECURITY.md)。

## 参与贡献

缺陷报告、翻译、主题和新的工具动词都欢迎——先看
[CONTRIBUTING.md](CONTRIBUTING.md)。里面介绍了各测试套件、如何添加一种语言，以及
那几条已经挡住真实 bug 的规则（日志里不放密钥、绝不合成回车、绝不让听写文本
变成指令）。

测试套件约有 486 条断言，分布在 13 个免费套件中，而且是往真实应用里打字，而不是
打给 mock。想帮忙的话，有很多事情可以做。

## 许可证

[MIT](LICENSE) Dependency and model licences: [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).——随你怎么用。

Jarvis 与 OpenAI 没有关联，也未获其认可或赞助。你需要自备 API 密钥，并对自己的
使用负责。

---

<div align="center">

**如果 Jarvis 帮你省下了时间，一颗 ⭐ 能让更多人发现它。**

</div>
