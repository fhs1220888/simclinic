# 20261012首版的GitHub同步范围

本目录提供已有 `0.11.0+collect1` 独立wheel、源码包、使用说明、补丁、精选证据和试用任务。原wheel与源码包字节保持不变，解压源码另放在 `release/first-release-collect1` 供查看。本次没有新增功能、案例或实验。

## 本地安装与演示

从仓库根目录进入 `releases/20261012`，使用Python3.9+、Git和Bash：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --no-index --no-deps ./simclinic-0.11.0+collect1-py3-none-any.whl
.venv/bin/simclinic showcase --out ./demo
```

打开 `demo/index.html`；逐步采集、已有补丁应用与比较见[README](README.md)和[WALKTHROUGH](WALKTHROUGH.md)。这些是既有操作说明，本次同步未重新执行命令。演示使用既有Shell控制例，CFD部分读取旧归档。模型、求解与采样调用均未增加。

## 封版与公开文件的区别

20261012两个完整封版ZIP继续保存在维护者本地，原摘要保持不变。原包有3份JSON回执包含本机用户目录路径；仓库CONTRIBUTING要求不提交个人路径，因此完整ZIP和这3份回执未公开上传。具体排除项见[PUBLICATION.json](PUBLICATION.json)，其余产品文件来自同一封版。

本目录采用独立的[SHA256SUMS](SHA256SUMS)。`SEALED-PRODUCT-SHA256SUMS`及证据目录的`SEALED-SHA256SUMS`保留原完整封版的清单，用于追溯，不应对本公开选集执行这些历史清单。原封版校验记录在[seal-records](seal-records/first-release-seal-verification-20261012.json)，证明的是原本地完整交付；本次公开选集检查见[PUBLIC-SYNC-VERIFICATION.json](PUBLIC-SYNC-VERIFICATION.json)。

原wheel：`14f6893e07e547a7d74fa2453c3ab0292d0d7dbc011613b4b6d14ece7ff962f3`。

原源码包：`e5cddf89bf2fd03ab27baf50ddec057e418f78e8aad9a0e864101d509d359f99`。

## 当前状态

首版本地交付已完成；首批独立用户验收仍待验证，联系、开始及反馈均0，任务与标准仅供后续恢复时使用。

仓库自有代码已有[MIT LICENSE](../../LICENSE)。封版包内NOTICE、STATUS及源码包许可说明保留其形成时的旧文字，与当前仓库许可存在差异；原包未重写，许可文案与分发元数据的一致性整理仍待后续。本次没有选择新许可证，也没有发布包索引或GitHub Release。
