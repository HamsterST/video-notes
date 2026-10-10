# 抖音适配、MOSS 转写与默认 GPU

## 本次变更

- 支持抖音单视频链接和包含 modal_id 的网页链接，归一化为相同视频与缓存身份。
- 命令行/Skill 支持 v.douyin.com 分享短链及含一条短链的分享文案；校验跳转站点、次数和链接类型，图文作品明确拒绝。扩展面板仍只处理已适配详情页，没有加入短链解析。
- 抖音通过移动端 feed 数据接口查询媒体与可用字幕轨，缺少独立字幕时下载原声 MP4 并转写。接口属于站点实现细节，后续可能变化；不把查询失败当作没有字幕。
- 字幕查询成功得到的媒体信息传给下载阶段复用，避免重复调用 yt-dlp.extract_info；已有完整媒体和转写缓存继续复用。
- 新增可选 MOSS 0.9B 后端，按需安装独立依赖，固定安装包和模型修订，输出时间戳、匿名说话人标签与真实来源信息。笔记规则保留说话人标签但不推断身份。
- CLI、扩展后台与转写后端默认使用 CUDA；显式 --device cpu 可选 CPU。没有可用 GPU 时明确失败，不静默回退。
- Windows Whisper 自动发现当前 Python 环境中 CUDA PyTorch 的 DLL，避免手动修改 PATH；系统运行库仍可使用。Whisper 使用 GPU FP16 / CPU INT8；MOSS 使用 GPU BF16（硬件支持时）或 FP16 / CPU FP32。
- README、Skill 和安装引导同步说明模型依赖、抖音支持范围及 GPU 条件。轻量安装仍不自动安装 CUDA。

## 验证

- 75 项现有及本次回归检查通过；覆盖抖音 URL、数据解析、下载、媒体信息复用、默认 CUDA 来源参数等。
- git diff --check 通过；当前运行环境 pip check 通过。
- 实际调用转写后端未传 device，未手动设置 PATH，成功使用 cuda 完成缓存视频的 Whisper small 转写（326 秒音频，识别约 16.4 秒）。
- MOSS CUDA 同视频完整转写成功，输出 203 段，最后时间 325.81 秒。

## 本机速度参考

RTX 2060 SUPER 8 GB，同一 325.84 秒视频；下载不计时。Whisper 使用 beam_size=1、VAD；CPU 8 线程 INT8，GPU FP16。MOSS 含时间戳与说话人区分，CPU FP32、GPU FP16。表格为识别阶段，功能并不完全相同。

| 模型 | CPU | GPU |
| --- | ---: | ---: |
| Whisper small | 61.18 秒 | 13.16 秒 |
| Whisper medium | 184.69 秒 | 23.81 秒 |
| MOSS 0.9B | 2210.70 秒 | 341.82 秒 |

这是单条视频的实测，不代表所有视频或机器；large-v3-turbo 和 large-v3 未测速。MOSS 运行中一次显卡总占用读数约 7.3 GB，不能保证更长视频在 8 GB 显存下可运行。多人区分质量未由本条单人视频验证。

## Git 提交范围

提交源码、扩展的既有抖音适配、规则、说明文档和回归检查。data、.venv、runtime.json、storage.json、outputs 已由 .gitignore 排除；模型权重、媒体、登录信息、安装包和个人默认模型不上传。

扩展现有修改是此前授权的抖音域名权限、Cookie 获取及 modal_id 详情页识别；本次 GPU 修改没有新增扩展界面或短链功能。

建议提交信息：feat: support Douyin and MOSS transcription with CUDA defaults
