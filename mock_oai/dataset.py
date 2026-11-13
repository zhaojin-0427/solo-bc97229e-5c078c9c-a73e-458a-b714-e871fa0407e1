"""演示用机构库数据集：确定性随机生成，两源有重叠 DOI、不同写法与不同题名。"""
from __future__ import annotations

import random

TOPICS = [
    "钙钛矿太阳能电池界面工程", "城市交通碳排放在线监测", "古籍文本自动标点",
    "联邦学习中的梯度泄露防御", "近海微塑料光谱识别", "高校图书馆数字人文服务",
    "锂金属负极枝晶抑制", "深度学习遥感影像分割", "方言语音合成语料构建",
    "蛋白质结构对比学习", "黄河流域水文耦合预报", "明代地方志知识图谱",
    "钙钛矿光伏组件老化评估", "知识图谱嵌入负采样", "图书馆空间使用热力分析",
    "低照度图像增强", "量子点发光二极管封装", "学术论文开放引用网络",
    "跨语言预训练模型对齐", "水库调度多目标优化",
]
SUFFIXES_A = ["的实证研究", "：方法与展望", "中的关键问题", "（综述）", "的新证据"]
SUFFIXES_B = ["再探", "的比较分析", "：一项复现研究", "视角下的再思考", "及其应用"]

# 部分 DOI 故意使用不同写法，验证归一化与忽略大小写合并
DOI_STYLES = ["url", "url_upper", "dx", "doi_colon", "bare_upper", "bare"]


def _doi(registrant: str, seq: int, style: str) -> str:
    bare = f"10.{registrant}/lib.2026.{seq:04d}"
    if style == "url":
        return f"https://doi.org/{bare}"
    if style == "url_upper":
        return f"https://doi.org/{bare.upper()}"
    if style == "dx":
        return f"http://dx.doi.org/{bare}"
    if style == "doi_colon":
        return f"doi:{bare.upper()}"
    if style == "bare_upper":
        return bare.upper()
    return bare


def build_dataset(
    source_code: str,
    seed: int,
    count: int,
    registrant: str,
    overlap: int = 0,
    overlap_registrant: str | None = None,
) -> list[dict]:
    """生成 count 条记录，其中大部分带 DOI，少数为无 DOI 记录，1 条初始已撤稿。

    overlap > 0 时，前 overlap 条带 DOI 记录复用另一来源前 overlap 个真实 DOI
    （归一化后相同，但原始写法与题名不同），用于演示跨源去重与出处合并。
    """
    rng = random.Random(seed)
    # 另一来源带 DOI 记录的原始下标（跳过无 DOI 位），保证引用的 DOI 真实存在
    other_doi_indices = [i for i in range(1, count + 1) if i % 11 != 0]
    records: list[dict] = []
    doi_index = 0
    for i in range(1, count + 1):
        topic = TOPICS[(i - 1) % len(TOPICS)]
        suffixes = SUFFIXES_A if source_code == "A" else SUFFIXES_B
        title = f"{topic}{suffixes[(i - 1) % len(suffixes)]}"
        has_doi = (i % 11 != 0)  # 每 11 条出现 1 条无 DOI 记录
        if has_doi:
            doi_index += 1
            if overlap and doi_index <= overlap and doi_index <= len(other_doi_indices):
                # 与另一来源共用同一 DOI（不同写法/大小写），题名仍不同
                reg = overlap_registrant or registrant
                seq = other_doi_indices[doi_index - 1]
            else:
                reg, seq = registrant, i
            style = DOI_STYLES[(i + seed) % len(DOI_STYLES)]
            doi = _doi(reg, seq, style)
        else:
            doi = None
        records.append(
            {
                "oai_id": f"oai:lib-{source_code.lower()}:paper/{i:04d}",
                "title": f"[{source_code}] {title}",
                "doi": doi,
                "deleted": i == count,  # 最后一条初始即撤稿
            }
        )
    rng.shuffle(records)  # 打乱顺序，分页采集才有意义
    return records
