#!/usr/bin/env python3
"""Build a taxonomy proposal from the vocabulary alone.

This deliberately ignores Candidate evidence, article counts, source counts,
confidence, and the current Candidate decision.  It answers a different
product question: which concepts in the observed vocabulary deserve to become
stable interest/filter labels, which spellings are true aliases, which useful
concepts should remain flexible display labels, and which strings are noise.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from taxonomy_catalog import compute_manifest_sha256


def tag(
    code: str,
    kind: str,
    name_zh: str,
    name_en: str,
    boundary: str,
    *,
    aliases: tuple[str, ...] = (),
    parent_code: str | None = None,
    entity_type: str = "",
    user_selectable: bool = True,
) -> dict[str, Any]:
    return {
        "code": code,
        "kind": kind,
        "name_zh": name_zh,
        "name_en": name_en,
        "aliases": list(aliases),
        "parent_code": parent_code,
        "entity_type": entity_type,
        "description": boundary,
        "prompt_description": f"使用边界：{boundary}。仅在该概念是文章核心时使用；不要仅凭顺带提及或词面相似打标。",
        "user_selectable": user_selectable,
        "filterable": True,
        "recommendable": True,
    }


CATALOG = [
    # Topic: stable, reusable concepts that a reader can intentionally follow.
    tag("topic.ai-agents", "topic", "AI 智能体", "AI Agents", "能够规划、调用工具并多步骤执行任务的智能体系统", aliases=("AI智能体", "智能体", "AI Agent", "Agent")),
    tag("topic.ai-coding", "topic", "AI 编程", "AI Coding", "代码生成、编程助手和 AI 辅助软件开发", aliases=("AI编程工具", "AI-assisted development", "AI-assisted Software Development")),
    tag("topic.ai-safety", "topic", "AI 对齐与安全", "AI Safety", "模型或智能体的对齐、越狱、滥用、失控与能力安全", aliases=("AI安全", "AI 安全", "AI对齐", "AI 对齐", "AI安全与对齐")),
    tag("topic.cybersecurity", "topic", "网络安全", "Cybersecurity", "网络、软件、系统和攻击防御安全；不等同于模型对齐安全", aliases=("网络安全",)),
    tag("topic.data-security-privacy", "topic", "数据安全与隐私", "Data Security & Privacy", "数据保护、隐私、机密计算和敏感信息治理", aliases=("数据安全", "数据隐私")),
    tag("topic.machine-learning", "topic", "机器学习", "Machine Learning", "非特指某一模型家族的机器学习方法与系统", aliases=("Machine Learning",)),
    tag("topic.natural-language-processing", "topic", "自然语言处理", "Natural Language Processing", "自然语言理解、生成和处理技术", aliases=("NLP",)),
    tag("topic.large-language-models", "topic", "大语言模型", "Large Language Models", "大语言模型本身的能力、架构和应用", aliases=("大模型", "LLM")),
    tag("topic.multimodal-models", "topic", "多模态模型", "Multimodal Models", "联合处理文本、图像、音频或视频的多模态模型", aliases=("多模态 AI", "多模态大模型", "多模态大语言模型")),
    tag("topic.generative-ai", "topic", "生成式 AI", "Generative AI", "跨模态生成内容的生成式模型与应用", aliases=("AIGC", "生成模型")),
    tag("topic.image-generation", "topic", "AI 图像生成", "AI Image Generation", "生成或编辑图像的 AI 模型与产品", aliases=("图像生成",), parent_code="topic.generative-ai"),
    tag("topic.video-generation", "topic", "AI 视频生成", "AI Video Generation", "生成或编辑视频的 AI 模型与产品", aliases=("AI视频生成", "AI 视频生成", "生成式视频"), parent_code="topic.generative-ai"),
    tag("topic.audio-generation", "topic", "AI 音频与音乐生成", "AI Audio & Music Generation", "生成语音、音频或音乐的 AI 模型与产品", aliases=("AI音乐生成",), parent_code="topic.generative-ai"),
    tag("topic.model-reasoning", "topic", "模型思考与推理", "Model Reasoning", "模型的思考、解题、规划和推理能力；不指部署侧 inference", aliases=("推理模型", "Reasoning Models")),
    tag("topic.probabilistic-ml", "topic", "概率机器学习", "Probabilistic Machine Learning", "贝叶斯推断、概率建模和不确定性推理", aliases=("Bayesian inference", "贝叶斯推断", "probabilistic reasoning")),
    tag("topic.model-training", "topic", "模型训练", "Model Training", "训练数据、训练流程、优化算法和训练基础设施", aliases=("大模型训练", "LLM Training")),
    tag("topic.pretraining", "topic", "预训练", "Pre-training", "模型预训练阶段与方法", aliases=("Pretraining",), parent_code="topic.model-training", user_selectable=False),
    tag("topic.post-training", "topic", "后训练", "Post-training", "监督微调、偏好优化和其他后训练方法", aliases=("Post Training",), parent_code="topic.model-training", user_selectable=False),
    tag("topic.ai-inference", "topic", "模型推断与部署", "Model Inference & Deployment", "模型执行推断、服务化、本地部署和运行时工程；中文“推理”不作无条件同义词", aliases=("LLM Inference", "大模型推断")),
    tag("topic.model-optimization", "topic", "模型优化与效率", "Model Optimization & Efficiency", "模型压缩、量化、剪枝、蒸馏、缓存和效率优化"),
    tag("topic.model-evaluation", "topic", "模型评估", "Model Evaluation", "模型、智能体、基准和能力的系统评估", aliases=("模型评估",)),
    tag("topic.model-interpretability", "topic", "模型可解释性", "Model Interpretability", "模型内部机制、行为解释和可解释性研究", aliases=("AI interpretability",)),
    tag("topic.reinforcement-learning", "topic", "强化学习", "Reinforcement Learning", "强化学习算法、训练和应用", aliases=("RL",)),
    tag("topic.continual-learning", "topic", "持续学习", "Continual Learning", "模型在持续数据或任务中学习且避免灾难性遗忘", aliases=("持续学习",)),
    tag("topic.open-source-ai", "topic", "开源与开放权重 AI", "Open-source & Open-weight AI", "开源模型、开放权重及其生态"),
    tag("topic.robotics", "topic", "机器人技术", "Robotics", "机器人本体、控制、协作和自主系统", aliases=("机器人", "Robot Technology")),
    tag("topic.embodied-ai", "topic", "具身智能", "Embodied AI", "智能体通过感知和动作与物理世界交互", aliases=("具身AI",)),
    tag("topic.human-computer-interaction", "topic", "人机交互", "Human-Computer Interaction", "人与 AI 或计算系统的交互界面、体验和协作", aliases=("人机交互", "HCI")),
    tag("topic.computer-vision", "topic", "计算机视觉", "Computer Vision", "图像或视频理解、识别和视觉建模", aliases=("计算机视觉",)),
    tag("topic.document-ai", "topic", "文档智能", "Document AI", "OCR、文档解析、PDF 理解和结构化抽取"),
    tag("topic.speech-ai", "topic", "语音 AI", "Speech AI", "语音识别、语音理解和语音交互"),
    tag("topic.ai-for-science", "topic", "AI for Science", "AI for Science", "AI 驱动的科学发现、实验自动化、科学模拟和研究"),
    tag("topic.ai-infrastructure", "topic", "AI 基础设施", "AI Infrastructure", "训练和推断所需的算力、云、数据中心和工程基础设施", aliases=("AI基础设施",)),
    tag("topic.ai-governance", "topic", "AI 治理与监管", "AI Governance & Regulation", "AI 法规、政策、合规、标准和公共治理"),
    tag("topic.ai-ethics-social-impact", "topic", "AI 伦理与社会影响", "AI Ethics & Social Impact", "AI 对就业、经济、公平、伦理和社会的影响"),
    tag("topic.enterprise-ai", "topic", "企业 AI", "Enterprise AI", "企业采用、部署和治理 AI 的产品与实践"),
    tag("topic.ai-business", "topic", "AI 商业与创业", "AI Business & Startups", "AI 创业、商业模式、投资和产业经济"),

    # Industry: where AI is applied or commercialized, not a technical method.
    tag("industry.software", "industry", "软件与开发工具", "Software & Developer Tools", "软件、开发工具和软件服务行业", aliases=("开发者工具",)),
    tag("industry.cloud-infrastructure", "industry", "云计算与数据中心", "Cloud & Data Center", "云平台、数据中心和计算基础设施行业", aliases=("云计算", "数据中心")),
    tag("industry.education", "industry", "教育", "Education", "教育、教育科技和学习服务行业"),
    tag("industry.healthcare", "industry", "医疗健康与生命科学", "Healthcare & Life Sciences", "医疗、医药、生物科技和生命科学行业", aliases=("医疗健康", "Healthcare", "医疗")),
    tag("industry.finance", "industry", "金融与保险", "Finance & Insurance", "银行、证券、支付、保险和金融科技行业", aliases=("金融", "Finance", "金融服务", "金融服务业", "保险业", "保险行业")),
    tag("industry.manufacturing", "industry", "制造业", "Manufacturing", "工业制造、工厂和生产体系", aliases=("工业制造",)),
    tag("industry.semiconductor", "industry", "半导体", "Semiconductor", "芯片、半导体设计制造和相关供应链", aliases=("芯片产业",)),
    tag("industry.robotics", "industry", "机器人产业", "Robotics Industry", "机器人公司、产品市场和产业链；无分面上下文的“机器人”不作为 Alias", aliases=("Robot Industry",)),
    tag("industry.government", "industry", "政府与公共部门", "Government & Public Sector", "政府、公共服务和公共基础设施", aliases=("政务",)),
    tag("industry.media-entertainment", "industry", "媒体与娱乐", "Media & Entertainment", "影视、游戏、音乐、内容制作和媒体行业"),
    tag("industry.energy-utilities", "industry", "能源与电力", "Energy & Utilities", "能源、电力、公用事业和电网行业", aliases=("能源电力",)),
    tag("industry.travel-hospitality", "industry", "旅游与出行", "Travel & Hospitality", "旅游、酒店、出行和旅行科技行业", aliases=("旅游行业", "travel technology")),
    tag("industry.chemicals", "industry", "化工", "Chemicals", "化工、材料和化学工业", aliases=("化工行业",)),
    tag("industry.cybersecurity", "industry", "网络安全产业", "Cybersecurity Industry", "网络安全产品、服务和产业市场；“网络安全”本身保留给 Topic"),

    # Entity: durable AI-native organizations, model/product families, protocols, and projects.
    tag("entity.openai", "entity", "OpenAI", "OpenAI", "OpenAI 组织及其组织级动态", entity_type="organization"),
    tag("entity.anthropic", "entity", "Anthropic", "Anthropic", "Anthropic 组织及其组织级动态", entity_type="organization"),
    tag("entity.google", "entity", "Google", "Google", "Google 组织及其 AI 业务", entity_type="organization"),
    tag("entity.google-deepmind", "entity", "Google DeepMind", "Google DeepMind", "Google DeepMind 研究组织", aliases=("DeepMind",), entity_type="organization"),
    tag("entity.microsoft", "entity", "微软", "Microsoft", "Microsoft 组织及其 AI 业务", aliases=("Microsoft",), entity_type="organization"),
    tag("entity.meta", "entity", "Meta", "Meta", "Meta 组织及其 AI 业务", entity_type="organization"),
    tag("entity.xai", "entity", "xAI", "xAI", "xAI 组织及其 AI 业务", entity_type="organization"),
    tag("entity.deepseek", "entity", "DeepSeek", "DeepSeek", "DeepSeek 组织；具体模型版本保留为灵活标签", aliases=("深度求索",), entity_type="organization"),
    tag("entity.moonshot-ai", "entity", "月之暗面", "Moonshot AI", "Moonshot AI（月之暗面）组织", aliases=("Moonshot",), entity_type="organization"),
    tag("entity.minimax", "entity", "MiniMax", "MiniMax", "MiniMax 组织；具体模型版本保留为灵活标签", entity_type="organization"),
    tag("entity.nvidia", "entity", "英伟达", "NVIDIA", "NVIDIA 组织及其 AI 计算平台", aliases=("NVIDIA",), entity_type="organization"),
    tag("entity.hugging-face", "entity", "Hugging Face", "Hugging Face", "Hugging Face 组织及其开源 AI 平台", entity_type="organization"),
    tag("entity.alibaba", "entity", "阿里巴巴", "Alibaba", "阿里巴巴组织及其 AI 业务", aliases=("阿里",), entity_type="organization"),
    tag("entity.alibaba-cloud", "entity", "阿里云", "Alibaba Cloud", "阿里云云计算与 AI 平台", aliases=("阿里云",), entity_type="product"),
    tag("entity.tencent", "entity", "腾讯", "Tencent", "腾讯组织及其 AI 业务", entity_type="organization"),
    tag("entity.huawei", "entity", "华为", "Huawei", "华为组织及其 AI 业务", entity_type="organization"),
    tag("entity.apple", "entity", "苹果", "Apple", "Apple 组织及其 AI 业务", entity_type="organization"),
    tag("entity.iflytek", "entity", "科大讯飞", "iFLYTEK", "科大讯飞组织及其 AI 业务", entity_type="organization"),
    tag("entity.ant-group", "entity", "蚂蚁集团", "Ant Group", "蚂蚁集团组织及其 AI 业务", entity_type="organization"),
    tag("entity.aws", "entity", "AWS", "AWS", "Amazon Web Services 云计算与 AI 平台", entity_type="product"),
    tag("entity.microsoft-azure", "entity", "Microsoft Azure", "Microsoft Azure", "Microsoft Azure 云计算与 AI 平台", aliases=("Azure",), entity_type="product"),
    tag("entity.google-cloud", "entity", "Google Cloud", "Google Cloud", "Google Cloud 云计算与 AI 平台", aliases=("Google GCP", "GCP"), entity_type="product"),
    tag("entity.github", "entity", "GitHub", "GitHub", "GitHub 开发协作平台", entity_type="product"),
    tag("entity.mlcommons", "entity", "MLCommons", "MLCommons", "MLCommons AI 基准与标准组织", entity_type="organization"),
    tag("entity.openmined", "entity", "OpenMined", "OpenMined", "OpenMined 隐私保护 AI 社区与组织", entity_type="organization"),
    tag("entity.apptronik", "entity", "Apptronik", "Apptronik", "Apptronik 机器人组织", entity_type="organization"),
    tag("entity.zhiyuan-robotics", "entity", "智元机器人", "AgiBot", "智元机器人组织", entity_type="organization"),
    tag("entity.chatgpt", "entity", "ChatGPT", "ChatGPT", "OpenAI 的 ChatGPT 产品", entity_type="product"),
    tag("entity.claude", "entity", "Claude", "Claude", "Anthropic Claude 模型家族；具体版本保留为灵活标签", entity_type="model"),
    tag("entity.claude-code", "entity", "Claude Code", "Claude Code", "Anthropic Claude Code 编程产品", entity_type="product"),
    tag("entity.codex", "entity", "Codex", "Codex", "OpenAI Codex 编程产品与模型家族", entity_type="product"),
    tag("entity.gemini", "entity", "Gemini", "Gemini", "Google Gemini 模型家族；具体版本保留为灵活标签", aliases=("Google Gemini",), entity_type="model"),
    tag("entity.gemini-robotics", "entity", "Gemini Robotics", "Gemini Robotics", "Google DeepMind Gemini Robotics 模型家族", entity_type="model"),
    tag("entity.qwen", "entity", "通义千问", "Qwen", "Qwen 模型家族；具体版本保留为灵活标签", aliases=("Qwen", "千问"), entity_type="model"),
    tag("entity.kimi", "entity", "Kimi", "Kimi", "Moonshot AI 的 Kimi 产品与模型家族", entity_type="product"),
    tag("entity.grok", "entity", "Grok", "Grok", "xAI Grok 模型家族；具体版本保留为灵活标签", entity_type="model"),
    tag("entity.alphafold", "entity", "AlphaFold", "AlphaFold", "AlphaFold 蛋白质结构预测模型家族", entity_type="model"),
    tag("entity.adobe-firefly", "entity", "Adobe Firefly", "Adobe Firefly", "Adobe Firefly 生成式 AI 产品", entity_type="product"),
    tag("entity.amazon-bedrock", "entity", "Amazon Bedrock", "Amazon Bedrock", "Amazon Bedrock 企业生成式 AI 平台", entity_type="product"),
    tag("entity.google-ai-studio", "entity", "Google AI Studio", "Google AI Studio", "Google AI Studio 开发产品", entity_type="product"),
    tag("entity.vllm", "entity", "vLLM", "vLLM", "vLLM 开源推断项目", entity_type="project"),
    tag("entity.firecrawl", "entity", "Firecrawl", "Firecrawl", "Firecrawl 开源网页数据项目", entity_type="project"),
    tag("entity.openclaw", "entity", "OpenClaw", "OpenClaw", "OpenClaw 开源项目", entity_type="project"),
    tag("entity.opencode", "entity", "OpenCode", "OpenCode", "OpenCode 开源 AI 编程项目", aliases=("opencode",), entity_type="project"),
    tag("entity.model-context-protocol", "entity", "模型上下文协议", "Model Context Protocol", "Model Context Protocol（MCP）协议", aliases=("MCP",), entity_type="protocol"),
]


# A narrower label may resolve into a broader canonical concept in closed-set
# tagging, but is intentionally NOT registered as an unconditional Alias.
BROADER: dict[str, str] = {
    "3D生成": "topic.generative-ai",
    "AI 无障碍": "topic.ai-ethics-social-impact",
    "AI安全评估": "topic.model-evaluation",
    "AI实时直播": "topic.video-generation",
    "AI对就业影响": "topic.ai-ethics-social-impact",
    "AI创业": "topic.ai-business",
    "AI广告": "topic.ai-business",
    "AI投资": "topic.ai-business",
    "AI智能体评估": "topic.model-evaluation",
    "AI模型发布": "topic.large-language-models",
    "AI监管": "topic.ai-governance",
    "AI硬件": "topic.ai-infrastructure",
    "AI算力": "topic.ai-infrastructure",
    "AI社会影响": "topic.ai-ethics-social-impact",
    "AI经济影响": "topic.ai-ethics-social-impact",
    "AI自进化": "topic.continual-learning",
    "AI芯片": "industry.semiconductor",
    "AI伦理": "topic.ai-ethics-social-impact",
    "AI工程平台": "topic.enterprise-ai",
    "AI产业经济": "topic.ai-business",
    "AI商业模式": "topic.ai-business",
    "云基础设施": "topic.ai-infrastructure",
    "云计算": "topic.ai-infrastructure",
    "产业AI": "topic.enterprise-ai",
    "人工智能助手": "topic.ai-agents",
    "人工智能政策": "topic.ai-governance",
    "人工智能教育": "industry.education",
    "人形机器人": "topic.robotics",
    "企业AI应用": "topic.enterprise-ai",
    "企业安全": "topic.cybersecurity",
    "具身智能": "topic.embodied-ai",
    "学术研究": "topic.ai-for-science",
    "游戏 AI": "industry.media-entertainment",
    "初创企业": "topic.ai-business",
    "加速器": "topic.ai-business",
    "医学影像": "industry.healthcare",
    "基础模型": "topic.large-language-models",
    "多智能体": "topic.ai-agents",
    "多机器人协作": "topic.robotics",
    "多模态生成": "topic.generative-ai",
    "大模型本地部署": "topic.ai-inference",
    "Data Augmentation": "topic.model-training",
    "Model Distillation": "topic.model-optimization",
    "基准污染": "topic.model-evaluation",
    "奖励黑客": "topic.ai-safety",
    "实验室自动化": "topic.ai-for-science",
    "工业具身智能": "topic.embodied-ai",
    "工具调用": "topic.ai-agents",
    "开放权重模型": "topic.open-source-ai",
    "开源": "topic.open-source-ai",
    "开源模型": "topic.open-source-ai",
    "开源软件": "topic.open-source-ai",
    "开源项目": "topic.open-source-ai",
    "强化学习": "topic.reinforcement-learning",
    "归一化流": "topic.generative-ai",
    "手语翻译": "topic.human-computer-interaction",
    "持续学习": "topic.continual-learning",
    "推理优化": "topic.model-optimization",
    "云计算市场": "industry.cloud-infrastructure",
    "政府AI": "topic.ai-governance",
    "政府合作": "industry.government",
    "教育": "industry.education",
    "教育技术": "industry.education",
    "教育科技": "industry.education",
    "数据中心": "topic.ai-infrastructure",
    "数据安全": "topic.data-security-privacy",
    "数据蒸馏": "topic.model-training",
    "数据隐私": "topic.data-security-privacy",
    "生物安全": "topic.ai-safety",
    "金融监管": "topic.ai-governance",
    "模型剪枝": "topic.model-optimization",
    "模型压缩": "topic.model-optimization",
    "模型安全": "topic.ai-safety",
    "模型效率": "topic.model-optimization",
    "模型行为": "topic.model-interpretability",
    "模型评估": "topic.model-evaluation",
    "模型量化": "topic.model-optimization",
    "数值精度": "topic.model-optimization",
    "机器人": "topic.robotics",
    "机器人学习": "topic.embodied-ai",
    "机密计算": "topic.data-security-privacy",
    "气象预测": "topic.ai-for-science",
    "OCR": "topic.document-ai",
    "PDF处理": "topic.document-ai",
    "物理渲染": "topic.computer-vision",
    "科学发现": "topic.ai-for-science",
    "科研支持": "topic.ai-for-science",
    "网络安全评估": "topic.cybersecurity",
    "能源电力": "industry.energy-utilities",
    "自动化研究": "topic.ai-for-science",
    "通用机器人": "topic.robotics",
    "视觉-语言-动作模型": "topic.embodied-ai",
    "视觉语言模型": "topic.multimodal-models",
    "视频推理": "topic.computer-vision",
    "视频理解": "topic.computer-vision",
    "计算基础设施": "topic.ai-infrastructure",
    "计算机使用智能体": "topic.ai-agents",
    "计算机视觉": "topic.computer-vision",
    "语音识别": "topic.speech-ai",
    "风险投资": "topic.ai-business",
    "智能体安全": "topic.ai-safety",
    "政策监管": "topic.ai-governance",
    "创业孵化": "topic.ai-business",
    "自研芯片": "industry.semiconductor",
    "EU AI Act": "topic.ai-governance",
    "自进化系统": "topic.continual-learning",
    "量化交易": "industry.finance",
    "金融科技": "industry.finance",
    "监管合规": "topic.ai-governance",
    "芯片": "industry.semiconductor",
    "LLM": "topic.large-language-models",
    "模型上下文协议": "entity.model-context-protocol",
    "阿里": "entity.alibaba",
}


# Useful article-facing labels that are valid but too narrow, version-specific,
# event-like, or ambiguous to become stable interest/filter entries in v1.
FLEX_ONLY = {
    "AI推理": "中文“推理”无法仅凭词面区分 reasoning 与 inference",
    "Token": "可能指模型 token、加密代币或商业计量单位，需上下文消歧",
    "Token业务": "商业含义依赖上下文，不进入稳定兴趣目录",
    "KV缓存": "有效工程概念，但作为模型优化的灵活细标签更合适",
    "mRNA癌症疫苗": "有效窄概念，保留灵活展示并由生命科学行业承接兴趣",
    "个性化医疗": "有效窄概念，保留灵活展示并由医疗行业承接兴趣",
    "人机交互": "可解析到规范 Topic，但保留原词面展示",
    "出口管制": "有效政策细分，保留灵活展示并由 AI 治理承接兴趣",
    "医学影像": "有效医疗细分，保留灵活展示并由医疗行业承接兴趣",
    "多尺度建模": "有效科学方法，但首版不单独开放兴趣",
    "就业": "过于宽泛，保留灵活展示并由社会影响承接兴趣",
    "开放权重模型": "有效细分，闭集可归开源 AI，但不是同义词",
    "开源模型": "有效细分，闭集可归开源 AI，但不是同义词",
    "影视剧集": "有效内容类型，但不是 AI 核心规范 Topic",
    "批判性思维": "有效概念但不是稳定 AI 技术标签",
    "操作系统": "有效技术概念但超出 AI 规范目录边界",
    "数字孪生": "有效工业技术细分，首版保留灵活展示",
    "时间序列预测": "有效机器学习任务，首版保留灵活展示",
    "文本水印": "有效安全/溯源细分，首版保留灵活展示",
    "模型发布": "事件类型而非稳定兴趣概念",
    "浮点运算精度": "有效工程细分，由模型优化承接兴趣",
    "电力短缺": "事件/议题细分，由能源行业承接兴趣",
    "硬件标准化": "有效产业议题，但首版保留灵活展示",
    "精密制造": "有效制造细分，由制造业承接兴趣",
    "药物定价": "有效行业议题，由医疗与生命科学承接兴趣",
    "虚拟细胞": "有效 AI for Science 细分，首版保留灵活展示",
    "自回归模型": "有效模型架构细分，首版保留灵活展示",
    "贝叶斯推断": "可解析到概率机器学习，同时保留原词面展示",
    "边审边播": "有效治理场景词，但边界依赖上下文",
    "零样本学习": "有效机器学习细分，首版保留灵活展示",
    "架构图生成": "有效应用场景，由生成式 AI 承接兴趣",
    "迷你主机": "有效硬件品类，但不是稳定 AI 兴趣概念",
    "3D生成": "有效生成细分，由生成式 AI 承接兴趣",
    "数据蒸馏": "有效训练细分，由模型训练承接兴趣",
    "模型剪枝": "有效优化细分，由模型优化承接兴趣",
    "模型压缩": "有效优化细分，由模型优化承接兴趣",
    "模型量化": "有效优化细分，由模型优化承接兴趣",
    "奖励黑客": "有效安全细分，由 AI 安全承接兴趣",
    "人形机器人": "有效机器人形态，由机器人技术承接兴趣",
    "多智能体": "有效智能体细分，由 AI 智能体承接兴趣",
    "视觉-语言-动作模型": "有效具身模型形态，由具身智能承接兴趣",
    "工业具身智能": "有效应用细分，由具身智能与制造业承接兴趣",
}


EXCLUDE = {
    "AI": "过宽，几乎不能区分内容或表达兴趣",
    "人工智能": "过宽，几乎不能区分内容或表达兴趣",
    "AI模型": "边界过宽且与多个模型 Topic 重叠",
    "人工智能应用服务商": "主体类别而非具体行业、Topic 或 Entity",
    "人工智能+行动": "不完整的事件/口号短语",
    "bugfix": "发布动作而非主题",
    "IPO": "通用公司事件，不是 AI 规范概念",
    "亿万富翁": "人物财富属性，不是 AI 规范概念",
    "产业培育": "泛政策措辞，缺少稳定概念边界",
    "住房政策": "与 AI 核心目录无直接稳定关系",
    "合作伙伴关系": "通用公司事件，不是可复用主题",
    "学生表现": "过于具体的结果指标",
    "影视剧": "内容类型而非 AI 规范主题",
    "微短剧管理": "一次性治理场景，保留文章自由标签即可",
    "时代周刊AI 100": "榜单/事件名称，不是稳定概念",
    "未成年人保护": "泛治理议题，单独做 AI 兴趣标签边界不足",
    "日本公共AI基础设施": "地区限定的一次性政策项目",
    "俄罗斯影响力行动": "具体事件/行为，不是稳定目录概念",
    "财富报告": "报告类型而非 AI 概念",
    "财务分析": "通用业务任务，当前目录不单独开放",
    "财报": "文档类型而非 AI 概念",
    "软件发布": "事件类型而非稳定概念",
    "版本发布": "事件类型而非稳定概念",
    "开发者故事": "内容栏目/体裁而非主题",
    "边审边播": "场景化短语且边界不稳定",
}


ENTITY_EXCLUDE = {
    "EVE Online": "非 AI 核心产品",
    "Nature": "出版物，不在 Entity 五类型内",
    "Peter Steinberger": "人物，不在 Entity 五类型内",
    "徐梦迪": "人物，不在 Entity 五类型内",
    "邓泰华": "人物，不在 Entity 五类型内",
    "王烁": "人物，不在 Entity 五类型内",
    "稚晖君": "人物，不在 Entity 五类型内",
    "郭毅可": "人物，不在 Entity 五类型内",
    "马斯克": "人物，不在 Entity 五类型内",
    "美国": "地点，不在 Entity 五类型内",
    "泰国": "地点，不在 Entity 五类型内",
    "深圳": "地点，不在 Entity 五类型内",
    "芯片": "通用行业概念，不是命名实体",
    "人工智能应用服务商": "主体类别，不是命名实体",
    "范式": "既可能是普通概念也可能指公司，仅凭标签无法消歧",
    "LLM": "模型类别而非命名实体，应纠正到 Topic",
}


def normalize(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def build_lookup() -> dict[str, list[str]]:
    collected: dict[str, set[str]] = defaultdict(set)
    for row in CATALOG:
        for label in [row["name_zh"], row["name_en"], *row["aliases"]]:
            collected[normalize(label)].add(row["code"])
    return {key: sorted(codes) for key, codes in collected.items()}


def classify_candidate(row: sqlite3.Row, lookup: dict[str, list[str]]) -> dict[str, Any]:
    label = row["label"]
    source_kind = row["proposed_kind"]
    exact_codes = lookup.get(normalize(label), [])
    exact_code = exact_codes[0] if len(exact_codes) == 1 else None
    target_code = exact_code or BROADER.get(label)
    catalog_by_code = {item["code"]: item for item in CATALOG}

    if target_code:
        target = catalog_by_code[target_code]
        same_facet = source_kind == target["kind"]
        canonical_names = {normalize(target["name_zh"]), normalize(target["name_en"])}
        is_exact_name = normalize(label) in canonical_names
        if not same_facet:
            decision = "facet_correction"
            relation = "exact" if exact_code else "broader"
        elif exact_code and is_exact_name:
            decision = "canonical_match"
            relation = "exact"
        elif exact_code:
            decision = "alias"
            relation = "equivalent"
        else:
            decision = "broader_match"
            relation = "broader"
        return {
            "candidate_id": row["id"],
            "label": label,
            "source_kind": source_kind,
            "current_status_for_audit_only": row["status"],
            "decision": decision,
            "relation": relation,
            "target_code": target_code,
            "click_behavior": "temporary_label_search" if decision == "broader_match" else "canonical_filter",
            "reason": "按词义与分面边界归入规范概念；该判断未使用文章或 evidence 数据。",
        }

    if label in ENTITY_EXCLUDE or label in EXCLUDE:
        return {
            "candidate_id": row["id"],
            "label": label,
            "source_kind": source_kind,
            "current_status_for_audit_only": row["status"],
            "decision": "exclude",
            "relation": None,
            "target_code": None,
            "click_behavior": "none",
            "reason": ENTITY_EXCLUDE.get(label) or EXCLUDE[label],
        }

    if label in FLEX_ONLY:
        return {
            "candidate_id": row["id"],
            "label": label,
            "source_kind": source_kind,
            "current_status_for_audit_only": row["status"],
            "decision": "flex_only",
            "relation": "broader" if label in BROADER else None,
            "target_code": BROADER.get(label),
            "click_behavior": "temporary_label_search",
            "reason": FLEX_ONLY[label],
        }

    if source_kind == "entity":
        return {
            "candidate_id": row["id"],
            "label": label,
            "source_kind": source_kind,
            "current_status_for_audit_only": row["status"],
            "decision": "flex_only",
            "relation": None,
            "target_code": None,
            "click_behavior": "temporary_label_search",
            "reason": "词面看似命名实体，但仅凭名称不足以稳定确认对象边界或 entity_type；保留文章灵活展示，待人工消歧。",
        }

    raise ValueError(f"unclassified non-entity Candidate {row['id']}: {source_kind} {label!r}")


def existing_tag_actions(tags: list[sqlite3.Row]) -> list[dict[str, Any]]:
    catalog_by_code = {row["code"]: row for row in CATALOG}
    replacements: dict[str, str] = {}
    actions = []
    for row in tags:
        proposal = catalog_by_code.get(row["code"])
        if proposal is None:
            replacement_code = replacements.get(row["code"])
            if replacement_code:
                actions.append({
                    "tag_id": row["id"],
                    "code": row["code"],
                    "action": "replace",
                    "replacement_code": replacement_code,
                    "reason": "新目录调整了规范边界或命名；迁移既有引用后废弃旧 code。",
                })
                continue
            actions.append({
                "tag_id": row["id"],
                "code": row["code"],
                "action": "deprecate",
                "reason": "不在标签集合治理版目录中；该记录本身还存在 kind/code 前缀错位或概念过宽。",
            })
            continue
        changes = {}
        for key in ("kind", "name_zh", "name_en", "entity_type"):
            if (row[key] or "") != (proposal[key] or ""):
                changes[key] = {"from": row[key] or "", "to": proposal[key] or ""}
        actions.append({
            "tag_id": row["id"],
            "code": row["code"],
            "action": "edit" if changes else "keep",
            "changes": changes,
        })
    return actions


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Taxonomy v1 标签集合治理提案",
        "",
        "> 本提案只使用当前数据库中 34 个规范标签与 330 个 Candidate 的**标签文本和申报分面**。",
        "> 不读取文章、证据数量、来源覆盖、日期、置信度或 Candidate 当前审核状态；因此它回答的是",
        "> “什么值得成为稳定规范标签”，不是“最近什么最热”。本文档为审核提案，不会自动修改数据库。",
        "",
        "## 结论",
        "",
        f"- 新目录共 **{summary['canonical_total']}** 个规范标签：Topic {summary['canonical_by_kind']['topic']}、Industry {summary['canonical_by_kind']['industry']}、Entity {summary['canonical_by_kind']['entity']}。",
        f"- 其中 **{summary['user_selectable_total']}** 个进入兴趣候选池；`pretraining`、`post-training` 保留为可筛选下位标签但不独立开放兴趣选择。",
        f"- 330 个 Candidate 已全部给出去向：{', '.join(f'{key} {value}' for key, value in summary['candidate_decisions'].items())}。",
        "- `alias` 仅用于真正等价的名称/翻译/缩写；上下位概念使用 `broader_match`，不会污染 Alias。",
        "- `flex_only` 是有效但过窄、版本化、事件化或仅凭名称无法消歧的标签：不参与长期兴趣和日报选文，但可在文章中展示并点击发起临时检索。",
        "- 目录不包含总括性的“AI/人工智能”，否则几乎所有文章都会命中，失去筛选价值。",
        "",
        "## 已确认的产品决策",
        "",
        "- 接受 Entity 的稳定 AI 组织、模型家族、产品、协议与项目范围；大学、人物、地点和一次性硬件型号不进入规范目录。",
        "- `pretraining`、`post-training` 是 `model-training` 的下位标签：`user_selectable=false`、`filterable=true`、`recommendable=true`。",
        "- 开放 `industry.cybersecurity`，中文固定为“网络安全产业”；Topic 继续使用“网络安全”。",
        "- `flex_only` 可点击进入临时标签检索，但不能加入长期兴趣，也不参与个性化日报选文。",
        "",
        "## 与上一版最重要的不同",
        "",
        "1. 不再因为 evidence 少或当前状态是 rejected 就否定一个语义成立的概念。`网络安全`、`模型评估`、`模型可解释性`、`AI for Science`、`开源与开放权重 AI` 等重新进入规范目录。",
        "2. 不把下位概念伪装成 Alias。例如模型量化、KV 缓存、模型剪枝属于“模型优化与效率”，但都不是它的同义词。",
        "3. Entity 不收人物、地点和出版物；模型具体版本与单次产品名优先保留为灵活标签，模型家族和稳定产品才进入规范目录。",
        "4. Topic `机器人技术` 可使用“机器人”Alias；Industry 只能使用“机器人产业/Robot Industry”等明确表达，修复当前跨分面冲突。",
        "5. 当前错误记录 `topic.ai`、`topic.ai.vendor` 建议废弃；它们在库里实际 kind=industry，且概念边界也不适合作为规范标签。",
        "",
        "## 规范 Topic",
        "",
        "| code | 中文 / English | 父标签 | 兴趣可选 | 主要边界 |",
        "|---|---|---|---:|---|",
    ]
    for row in payload["catalog"]:
        if row["kind"] != "topic":
            continue
        lines.append(f"| `{row['code']}` | {row['name_zh']} / {row['name_en']} | {f'`{row["parent_code"]}`' if row['parent_code'] else '—'} | {'是' if row['user_selectable'] else '否'} | {row['description']} |")
    lines += ["", "## 规范 Industry", "", "| code | 中文 / English | 主要边界 |", "|---|---|---|"]
    for row in payload["catalog"]:
        if row["kind"] == "industry":
            lines.append(f"| `{row['code']}` | {row['name_zh']} / {row['name_en']} | {row['description']} |")
    lines += ["", "## 规范 Entity", "", "| code | 名称 | entity_type |", "|---|---|---|"]
    for row in payload["catalog"]:
        if row["kind"] == "entity":
            lines.append(f"| `{row['code']}` | {row['name_zh']} / {row['name_en']} | `{row['entity_type']}` |")
    lines += [
        "",
        "## Candidate 治理口径",
        "",
        "| 决策 | 产品语义 | 长期兴趣 | 文章展示 | 点击行为 |",
        "|---|---|---:|---:|---|",
        "| `canonical_match` | 已是某个规范名称 | 是 | 是 | 规范标签筛选 |",
        "| `alias` | 与规范名真正等价 | 解析到规范标签 | 是 | 规范标签筛选 |",
        "| `facet_correction` | 概念成立但申报分面错误 | 纠正后进入 | 是 | 规范标签筛选 |",
        "| `broader_match` | 有效下位概念，闭集时由更宽标签承接 | 通过父级 | 原词可灵活显示 | 临时标签检索 |",
        "| `flex_only` | 有效但过窄、版本化、事件化或歧义 | 否 | 是 | 临时标签检索 |",
        "| `exclude` | 噪声、人物、地点、体裁、通用事件或过宽词 | 否 | 否 | 无 |",
        "",
        "完整 330 项逐条去向、Alias、父子关系、Entity 类型和现有标签迁移动作见同名 JSON。",
        "",
        "## 建议的上线方式",
        "",
        "1. 全新库先执行 schema migration，再由仓库批准目录生成目标库 review 并运行 validation-only。",
        "2. 校验通过后导入 96 项及审核回执；脚本不自行发布，管理员仍需在管理面点击一次“发布 Taxonomy v1”。",
        "3. 发布成功后才开启文章分析。需要给历史文章补充灵活标签时执行 `full_analysis`，而不是仅 retag。",
        "4. 已经发布旧 v1 的开发库使用受限的 active-v1 同步模式；它只清理两个已知错误标签和歧义 Alias，并保留 Candidate 状态。",
        "",
        "完整命令、安全边界与验收点见 `taxonomy-v1-deployment.md`。",
        "",
        "## 产品决策状态",
        "",
        "上述四项已于 2026-09-02 确认。当前状态是**产品决策已固化为仓库批准目录，目标库安装脚本与灵活标签精确临时检索已实现**；具体环境仍需按上线手册执行导入、人工发布和历史回填。",
    ]
    return "\n".join(lines) + "\n"


def validate_payload(payload: dict[str, Any], candidate_ids: list[int]) -> None:
    catalog = payload["catalog"]
    reviews = payload["candidate_reviews"]
    codes = [row["code"] for row in catalog]
    if len(codes) != len(set(codes)):
        raise ValueError("duplicate canonical code")
    code_set = set(codes)
    for row in catalog:
        if not row["code"].startswith(f"{row['kind']}."):
            raise ValueError(f"facet/code prefix mismatch: {row['code']}")
        if row["parent_code"] and row["parent_code"] not in code_set:
            raise ValueError(f"unknown parent: {row['code']} -> {row['parent_code']}")
        if row["kind"] == "entity" and row["entity_type"] not in {
            "organization", "product", "model", "protocol", "project",
        }:
            raise ValueError(f"invalid entity_type: {row['code']}")

    lookup = build_lookup()
    collisions = {label: targets for label, targets in lookup.items() if len(targets) > 1}
    if collisions:
        raise ValueError(f"cross-code name/Alias collisions: {collisions}")
    if "机器人" in next(row for row in catalog if row["code"] == "industry.robotics")["aliases"]:
        raise ValueError("industry.robotics must not claim the ambiguous 机器人 Alias")
    catalog_by_code = {row["code"]: row for row in catalog}
    for child_code in ("topic.pretraining", "topic.post-training"):
        child = catalog_by_code[child_code]
        if child["parent_code"] != "topic.model-training":
            raise ValueError(f"training child has wrong parent: {child_code}")
        if child["user_selectable"] or not child["filterable"] or not child["recommendable"]:
            raise ValueError(f"training child flags violate the confirmed product decision: {child_code}")
    cyber_industry = catalog_by_code["industry.cybersecurity"]
    if cyber_industry["name_zh"] != "网络安全产业" or not cyber_industry["user_selectable"]:
        raise ValueError("industry.cybersecurity must be open with the unambiguous Chinese name")
    if any(not row["user_selectable"] for row in catalog if row["kind"] == "entity"):
        raise ValueError("accepted Entity catalog entries must remain user-selectable")
    if {row["candidate_id"] for row in reviews} != set(candidate_ids):
        raise ValueError("Candidate review coverage does not match the input label set")
    for row in reviews:
        if row["target_code"] and row["target_code"] not in code_set:
            raise ValueError(f"unknown review target: {row['candidate_id']} -> {row['target_code']}")
        expected_click = (
            "temporary_label_search"
            if row["decision"] in {"broader_match", "flex_only"}
            else "none" if row["decision"] == "exclude" else "canonical_filter"
        )
        if row["click_behavior"] != expected_click:
            raise ValueError(f"wrong click behavior for Candidate {row['candidate_id']}")

    serialized = json.dumps(payload, ensure_ascii=False)
    forbidden = {
        "support_article_count",
        "distinct_source_count",
        "distinct_day_count",
        "mean_confidence",
        "sample_article_ids",
        "context_excerpt",
    }
    leaked = sorted(key for key in forbidden if key in serialized)
    if leaked:
        raise ValueError(f"article/evidence fields leaked into label-only proposal: {leaked}")


def build_approved_catalog(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the portable, database-independent production artifact."""

    entries = [
        {
            "decision": "accept",
            **{
                key: row[key]
                for key in (
                    "code",
                    "kind",
                    "name_zh",
                    "name_en",
                    "aliases",
                    "parent_code",
                    "entity_type",
                    "description",
                    "prompt_description",
                    "user_selectable",
                    "filterable",
                    "recommendable",
                )
            },
        }
        for row in payload["catalog"]
    ]
    approved_core = {
        "schema_version": "taxonomy-v1-approved-catalog-v2",
        "status": "product_approved",
        "approved_at": "2026-09-02",
        "review_basis": "label_set_only",
        "coverage_decision": "not_applicable",
        "unmatched_candidate_policy": "fail",
        "product_decisions": payload["product_decisions"],
        "entries": entries,
    }
    manifest_sha256 = compute_manifest_sha256(approved_core)
    return {
        **approved_core,
        "manifest_sha256": manifest_sha256,
        "coverage": {
            "mode": "not_applicable",
            "sampled_source_count": 0,
            "manifest_source_count": 0,
            "article_count": 0,
            "candidate_count": 0,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/cms_data.db"))
    parser.add_argument(
        "--json-output",
        type=Path,
        default=Path("data/taxonomy-review/taxonomy-v1-label-only-proposal.json"),
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=Path("data/taxonomy-review/taxonomy-v1-label-only-proposal.md"),
    )
    parser.add_argument(
        "--catalog-output",
        type=Path,
        default=Path("data/taxonomy-review/taxonomy-v1-approved-catalog.json"),
    )
    args = parser.parse_args()

    connection = sqlite3.connect(f"file:{args.database.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    candidates = connection.execute(
        "SELECT id, label, normalized_label, proposed_kind, status "
        "FROM cms_tag_candidates ORDER BY id"
    ).fetchall()
    tags = connection.execute(
        "SELECT id, code, kind, name_zh, name_en, entity_type, status "
        "FROM cms_tags ORDER BY id"
    ).fetchall()
    connection.close()

    vocabulary = [
        {"id": row["id"], "label": row["label"], "kind": row["proposed_kind"]}
        for row in candidates
    ]
    vocabulary_hash = hashlib.sha256(
        json.dumps(vocabulary, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    lookup = build_lookup()
    reviews = [classify_candidate(row, lookup) for row in candidates]
    decision_counts = Counter(row["decision"] for row in reviews)
    canonical_counts = Counter(row["kind"] for row in CATALOG)
    selectable_counts = Counter(row["kind"] for row in CATALOG if row["user_selectable"])
    payload = {
        "schema_version": "taxonomy-v1-label-only-proposal-v1",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "product_approved_release_asset_generated",
        "product_decisions": {
            "confirmed_at": "2026-09-02",
            "entity_scope": "accepted_stable_ai_organizations_model_families_products_protocols_projects",
            "entity_exclusions": ["universities", "people", "places", "one-off hardware SKUs"],
            "training_children": {
                "codes": ["topic.pretraining", "topic.post-training"],
                "parent_code": "topic.model-training",
                "user_selectable": False,
                "filterable": True,
                "recommendable": True,
            },
            "cybersecurity_industry": {
                "accepted": True,
                "code": "industry.cybersecurity",
                "name_zh": "网络安全产业",
                "topic_boundary": "topic.cybersecurity remains 网络安全",
            },
            "flex_only_interaction": {
                "article_display": True,
                "click_behavior": "temporary_label_search",
                "long_term_interest": False,
                "personal_digest_selection": False,
                "implementation_status": "pending",
            },
        },
        "method": {
            "uses": ["cms_tags names/codes/facets", "cms_tag_candidates labels/proposed_kind"],
            "explicitly_ignores": [
                "articles",
                "candidate evidence",
                "support counts",
                "source counts",
                "date counts",
                "confidence",
                "nearest-tag similarity",
                "current candidate status as a decision input",
            ],
            "input_label_set_sha256": vocabulary_hash,
            "candidate_count": len(candidates),
            "existing_canonical_count": len(tags),
        },
        "principles": {
            "canonical": "stable, reusable, distinguishable, and meaningful as an interest/filter choice",
            "alias": "only lexical equivalence, translation, spelling variant, or unambiguous abbreviation",
            "broader_match": "narrow concept may resolve to a broader canonical tag but is never registered as its Alias",
            "flex_only": "valid article label that is too narrow, versioned, event-like, or ambiguous for the interest catalog",
            "exclude": "noise, person, place, publication, content type, generic event, or concept too broad to discriminate",
        },
        "summary": {
            "canonical_total": len(CATALOG),
            "canonical_by_kind": dict(sorted(canonical_counts.items())),
            "user_selectable_total": sum(selectable_counts.values()),
            "user_selectable_by_kind": dict(sorted(selectable_counts.items())),
            "candidate_total": len(reviews),
            "candidate_decisions": dict(sorted(decision_counts.items())),
        },
        "catalog": CATALOG,
        "existing_tag_actions": existing_tag_actions(tags),
        "candidate_reviews": reviews,
        "known_cleanup": [
            {
                "action": "remove_alias",
                "tag_code": "industry.robotics",
                "alias": "机器人",
                "reason": "无分面上下文时与 topic.robotics 冲突；Industry 只接受“机器人产业”等明确表达。",
            },
            {
                "action": "deprecate",
                "tag_codes": ["topic.ai", "topic.ai.vendor"],
                "reason": "当前记录 kind=industry 但 code 前缀为 topic，且概念过宽或不是有效分面概念。",
            },
        ],
    }

    validate_payload(payload, [row["id"] for row in candidates])
    approved_catalog = build_approved_catalog(payload)
    for output_path in (args.json_output, args.markdown_output, args.catalog_output):
        output_path.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown_output.write_text(render_markdown(payload), encoding="utf-8")
    args.catalog_output.write_text(
        json.dumps(approved_catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
