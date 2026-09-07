/* UI language layer (default zh-CN, optional EN).
 *
 * Two mechanisms, both Chinese-first:
 *
 * 1) Static chrome: HTML keeps Chinese as the default and carries the English on the
 *    element itself (data-i18n-en / data-i18n-ph-en / data-i18n-title-en). Switching
 *    snapshots the original text and restores it later.
 *
 * 2) Dynamic chrome (JS-rendered tables/cards/buttons/pagination): renderers keep
 *    emitting Chinese. When the locale is "en" a MutationObserver translates the fixed
 *    UI vocabulary of every newly inserted text node (longest-match dictionary +
 *    numeric patterns). Switching back to "zh" runs the same pass in reverse, so no
 *    app.js call sites need to change.
 *
 * Data/content regions (LLM markdown, reasoning, transcripts, abstracts, evidence,
 * user-typed collection/chat names) are excluded so they are never touched.
 */
(function () {
  const KEY = "apex_ui_lang";
  const SUPPORTED = ["zh", "en"];
  const CJK = /[㐀-鿿]/;

  function current() {
    try {
      const v = localStorage.getItem(KEY);
      return SUPPORTED.includes(v) ? v : "zh";
    } catch {
      return "zh";
    }
  }

  // ------------------------------------------------------------ static chrome
  const originals = new Map();

  function snapshotStatic(node) {
    if (originals.has(node)) return;
    const rec = {};
    if (node.hasAttribute("data-i18n-en")) rec.text = node.textContent;
    if (node.hasAttribute("data-i18n-ph-en")) rec.placeholder = node.getAttribute("placeholder") || "";
    if (node.hasAttribute("data-i18n-title-en")) rec.title = node.getAttribute("title") || "";
    originals.set(node, rec);
  }

  function applyStatic(locale) {
    document.querySelectorAll("[data-i18n-en], [data-i18n-ph-en], [data-i18n-title-en]").forEach((node) => {
      snapshotStatic(node);
      const orig = originals.get(node);
      const staticText = orig.text !== undefined && [orig.text, node.getAttribute("data-i18n-en")].includes(node.textContent);
      if (locale === "en") {
        if (staticText) node.textContent = node.getAttribute("data-i18n-en");
        if (orig.placeholder !== undefined) node.setAttribute("placeholder", node.getAttribute("data-i18n-ph-en"));
        if (orig.title !== undefined) node.setAttribute("title", node.getAttribute("data-i18n-title-en"));
      } else {
        if (staticText) node.textContent = orig.text;
        if (orig.placeholder !== undefined) node.setAttribute("placeholder", orig.placeholder);
        if (orig.title !== undefined) node.setAttribute("title", orig.title);
      }
    });
  }

  // -------------------------------------------------- dynamic chrome vocabulary
  // Fixed UI phrases emitted by JS renderers (zh -> en).
  const CHROME = {
    // shared
    "查看摘要": "View abstract",
    "暂无数据": "No data",
    "暂无主题数据": "No topic data",
    "暂无 venue 数据": "No venue data",
    "加载中…": "Loading…",
    "上一页": "Previous",
    "下一页": "Next",
    "全部": "All",
    "取消": "Cancel",
    "重置": "Reset",
    "应用筛选": "Apply filters",
    "全选": "Select all",
    "全选结果": "Select all results",
    "清除": "Clear",
    // chat
    "思考过程": "Reasoning",
    "工具轨迹": "Tool trace",
    "重试": "Retry",
    "重新生成": "Regenerate",
    "编辑并重发": "Edit & resend",
    "历史版本": "History",
    "新对话": "New chat",
    "已归档": "Archived",
    "归档": "Archive",
    "删除": "Delete",
    "重命名": "Rename",
    "发送": "Send",
    "停止": "Stop",
    "证据片段": "Evidence",
    "回答被截断（max_tokens 不足）": "Truncated (max_tokens reached)",
    "未生成回答（未配置生成模型）。": "No answer (no generator configured).",
    // catalog / papers
    "论文目录": "Paper catalog",
    "论文实体": "Paper entity",
    "主记录": "Canonical",
    "作者": "Author",
    "作者未知": "Unknown author",
    "年份": "Year",
    "状态": "Status",
    "摘要": "Abstract",
    "缺少摘要": "No abstract",
    "暂未获得可追溯摘要": "No traceable abstract yet",
    "会议": "Conference",
    "期刊": "Journal",
    "预印本": "Preprint",
    "最终": "Final",
    "滚动": "Rolling",
    "检索": "Search",
    // entities / merge
    "待复核": "Pending review",
    "查看构成论文": "View papers",
    // topics / insights
    "未分类": "Unclassified",
    "研究主题": "Research topic",
    "技术标签": "Method tags",
    "高置信": "High confidence",
    "中置信": "Medium confidence",
    "边界": "Boundary",
    // collections / pipeline
    "集合详情": "Collection detail",
    "我的论文集合": "My paper collections",
    "刷新状态": "Refresh status",
    "运行所选": "Run selected",
    "下载": "Download",
    "解析": "Parse",
    "入库": "Ingest",
    "全部删除": "Delete all",
    "清理PDF": "Clean PDFs",
    "清理解析": "Clean parses",
    "清理入库": "Clean ingest",
    // overlays
    "确认": "Confirm",
    "关闭": "Close",
    "确定": "OK",
    // tables / labels picked up by DOM scan
    "为什么需要复核": "Why review is needed",
    "关系": "Relation",
    "分类版本": "Classification version",
    "主题标签关系": "Topic-tag relation",
    "← 返回数据洞察概览": "← Back to Data Insights overview",
    "允许同一论文属于多个研究方向": "One paper may belong to several directions",
    "同方向的多标签比例低于阈值": "Same-direction multi-label share below threshold",
    "Abstract元数据": "Abstract metadata",
    "查看构建报告进一步核对": "Check the build report for details",
    "同一 DOI 出现在多个 venue，保留清单记录并单独识别": "Same DOI across venues — kept as list records and flagged",
    "同一标识符对应的题名或Author不一致，禁止自动归并": "Same ID but mismatched title/authors — auto-merge disabled",
    // taxonomy (mirrors config/topics.json names; keep in sync when the tree changes)
    "机器学习基础与方法": "Machine Learning Foundations",
    "学习范式": "Learning paradigms",
    "架构与表征": "Architectures & representations",
    "训练理论与优化": "Training theory & optimization",
    "评测基准与数据方法": "Benchmarks & data-centric methods",
    "大语言模型（本体）": "Large Language Models (core)",
    "预训练与对齐": "Pretraining & alignment",
    "推理与规划": "Reasoning & planning",
    "效率与部署": "Efficiency & deployment",
    "LLM 评测": "LLM evaluation",
    "智能体与工具使用（本体）": "Agents & Tool Use (core)",
    "Agent 机制": "Agent mechanisms",
    "多智能体协作": "Multi-agent collaboration",
    "RAG 与知识增强": "RAG & knowledge grounding",
    "Agent 评测与基建": "Agent evaluation & infrastructure",
    "自然语言处理任务": "NLP Tasks",
    "翻译与多语": "Translation & multilingual",
    "摘要与生成": "Summarization & generation",
    "问答与对话": "Question answering & dialogue",
    "信息抽取与知识": "Information extraction & knowledge",
    "文本分类与情感": "Text classification & sentiment",
    "代码生成": "Code generation",
    "低资源与语篇适配": "Low-resource & discourse adaptation",
    "图像与视频底层视觉": "Low-level Vision (Image & Video)",
    "图像超分辨率": "Image super-resolution",
    "图像复原": "Image restoration",
    "图像去噪": "Image denoising",
    "图像去雨": "Image deraining",
    "图像去模糊": "Image deblurring",
    "图像去雾": "Image dehazing",
    "去划痕与伪影": "Scratch & artifact removal",
    "通用图像复原": "General image restoration",
    "图像增强": "Image enhancement",
    "计算成像与逆问题": "Computational imaging & inverse problems",
    "视频底层处理": "Video low-level processing",
    "图像与视频生成编辑": "Image & Video Generation / Editing",
    "文生图与视频生成": "Text-to-image & video generation",
    "图像编辑与重绘": "Image editing & inpainting",
    "可控与素材生成": "Controllable & asset generation",
    "图像高层理解与视频理解": "High-level Vision & Video Understanding",
    "目标检测": "Object detection",
    "图像分割": "Image segmentation",
    "识别与检索": "Recognition & retrieval",
    "姿态动作与跟踪": "Pose, action & tracking",
    "视频场景理解": "Video scene understanding",
    "三维视觉与重建": "3D Vision & Reconstruction",
    "几何重建": "Geometric reconstruction",
    "神经渲染": "Neural rendering",
    "3D 生成": "3D generation",
    "多模态与具身智能": "Multimodal & Embodied AI",
    "VLM 理解推理": "VLM understanding & reasoning",
    "音视频多模态": "Audio-visual multimodal",
    "具身智能": "Embodied intelligence",
    "自动驾驶": "Autonomous driving",
    "语音与音频": "Speech & Audio",
    // family descriptions (mirror config/topics.json root descriptions)
    "研究对象 = 通用方法/模型本身（非具体任务应用）。": "The subject is the general method/model itself (not a concrete task application).",
    "研究对象 = LLM 与通用能力本身。只收本体论文，用法仅打标签。": "The subject is the LLM and its general capabilities; core papers only, usage is merely tagged.",
    "研究对象 = Agent/RAG 系统本身。只收本体；CV/NLP 里用 agent 的只打 agentic 标签。": "The subject is the agent/RAG system itself; core only — agent use in CV/NLP tasks is tagged agentic.",
    "研究对象 = 具体语言任务（应用侧），非模型本体。": "The subject is concrete language tasks (application side), not the model core.",
    "CVPR low-level vision + computational imaging。超分单列方向；复原含复原子叶。": "CVPR low-level vision + computational imaging; SR is its own direction, restoration has sub-leaves.",
    "文生图/视频、条件生成、图像编辑、可控与素材生成。": "Text-to-image/video, conditional generation, image editing, controllable & asset generation.",
    "检测、分割、识别检索、姿态动作、跟踪、视频场景理解。": "Detection, segmentation, recognition/retrieval, pose/action, tracking, video scene understanding.",
    "几何重建、神经渲染、3D 生成。": "Geometric reconstruction, neural rendering, 3D generation.",
    "VLM 理解推理、音视频多模态、具身、自动驾驶。": "VLM understanding/reasoning, audio-visual multimodal, embodied, autonomous driving.",
    "ASR、TTS、语音增强、音频生成、说话人。": "ASR, TTS, speech enhancement, audio generation, speaker ID.",
    // remaining headers / labels / prose picked up by the EN DOM scan
    "单记录": "Single record",
    "手动": "Manual",
    "实体归并": "Entity merging",
    "已关联记录": "Linked records",
    "已归并实体": "Merged entities",
    "年度清单": "Yearly lists",
    "顶级主题": "Top-level topics",
    "论文清单记录": "Paper catalog records",
    "统一Paper entity": "Unified paper entity",
    "已覆盖Paper entity": "Covered paper entities",
    "跨 Venue DOI": "Cross-venue DOI",
    "格式警告": "Format warnings",
    "阻塞错误": "Blocking errors",
    "清单Status": "List status",
    "清单记录": "Catalog records",
    "研究实体": "Research entities",
    "目录元数据": "Catalog metadata",
    "数据库完整性": "Database integrity",
    "构建时间": "Build time",
    "更新时间": "Updated at",
    "源 CSV": "Source CSV",
    "我的集合": "My collections",
    "来源：": "Source:",
    "数量": "Count",
    "成员": "Members",
    "记录 A": "Record A",
    "记录 B": "Record B",
    "记录": "Records",
    "论文": "Paper",
    "归并依据": "Merge evidence",
    "等级": "Level",
    "类型": "Type",
    "来源": "Source",
    "标识": "ID",
    "操作": "Actions",
    "解释": "Explanation",
    "集合": "Collection",
    "已知重复出版或需解释Relation": "Known duplicates or relations needing explanation",
    "当前构建无阻塞问题": "No blocking issues in this build",
    "当前仅采用可审计强证据": "Only auditable strong evidence is used",
    "所有目录记录均已映射": "All catalog records are mapped",
    "规则和数据版本可追溯": "Rules and data versions are traceable",
    "需要核对，但不阻塞浏览": "Needs a look, but does not block browsing",
    "题名与Author完全一致，但可能是Journal扩展版，需人工判断": "Exact title & author match but may be a journal extension — needs human judgment",
    "查看已发现的非相关 / 弱相关样本": "View flagged irrelevant / weakly-related samples",
    "构成论文 →": "View papers →",
    "不OK关系不自动归并": "Non-OK relations are not auto-merged",
    "原始记录不Delete、不覆盖": "Source records are never deleted or overwritten",
    "大模型 · Agent · 图像生成与恢复": "LLMs · Agents · Image generation & restoration",
    "待人工复核": "Awaiting review",
    "＋ 新建": "+ New",
    "统一论文实体": "Unified paper entity",
    "已覆盖论文实体": "Covered paper entities",
    "原始记录不删除、不覆盖": "Source records are never deleted or overwritten",
    "不确定关系不自动归并": "Uncertain relations are not auto-merged",
    "已知重复出版或需解释关系": "Known duplicates or relations needing explanation",
    "同一标识符对应的题名或作者不一致，禁止自动归并": "Same ID but mismatched title/authors — auto-merge disabled",
    "每个结果可下钻查看命中依据": "Every result can be drilled into to see the matching evidence",
    "检索实体": "Search entities",
    "论文标题或 entity ID": "Paper title or entity ID",
    // evaluation page labels
    "固定样本": "Fixed samples",
    "已审阅": "Reviewed",
    "初步精度": "Precision",
    "高分": "High",
    "中分": "Middle",
    "相关": "Relevant",
    "仅提及": "Mention only",
    "错误信号": "Wrong signal",
    "待定": "Uncertain",
    "非 LLM Agent": "Non-LLM agent",
    "偶然提及": "Incidental mention",
    "非核心方法": "Incidental method",
    "范围过宽": "Scope too broad",
    "缩写冲突": "Acronym collision",
    "仅作评测": "Benchmark only",
    "模态冲突": "Modality collision",
    "暂无已分组误差": "No grouped errors yet",
    "当前未发现问题样本": "No flagged samples yet",
    "3 个主题 · 每类 100 条": "3 topics · 100 each",
    "主题": "Topic",
    "已审 / 样本": "Reviewed / Sampled",
    "总体": "Overall",
    "高 / 中 / 边界": "High / Mid / Boundary",
    "Venue / 年份": "Venue / Year",
    "弱标签得分": "Weak-label score",
    "命中依据": "Evidence",
    "主题论文": "Topic papers",
    "构成论文": "Constituent papers",
    "（族根主题构成）": "(constituent topics of this family)",
    "清单状态": "List status",
    "已分类": "Classified",
    "多标签率": "Multi-label rate",
    "构建通过": "Build passed",
    "评估版本：": "Evaluation version: ",
    "分类版本：": "Classification version: ",
    "当前筛选条件下没有主题论文": "No topic papers under the current filters",
    "当前筛选条件下没有论文记录": "No paper records under the current filters",
    "当前筛选条件下没有论文实体": "No paper entities under the current filters",
    "初步精度仅基于已审阅且非 uncertain 的样本；当前为模型辅助标注，不是人工金标准。": "Precision is computed only on reviewed, non-uncertain samples; labels are model-assisted, not a human gold standard.",

  };
  const REVERSE = Object.fromEntries(Object.entries(CHROME).map(([zh, en]) => [en, zh]));

  function esc(s) {
    return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }
  const EN_RE = new RegExp(Object.keys(CHROME).sort((a, b) => b.length - a.length).map(esc).join("|"), "g");
  const ZH_RE = new RegExp(Object.keys(REVERSE).sort((a, b) => b.length - a.length).map(esc).join("|"), "g");

  // composite/numeric phrases: { toEn: RegExp, toZh: RegExp, en: fmt, zh: fmt }
  const PATTERNS = [
    { toEn: /共\s*([\d,]+)\s*条\s*[·・]\s*第\s*(\d+)\s*\/\s*(\d+)\s*页/, toZh: /([\d,]+)\s*results\s*·\s*page\s*(\d+)\s*\/\s*(\d+)/, en: "$1 results · page $2 / $3", zh: "共 $1 条 · 第 $2 / $3 页" },
    { toEn: /共\s*([\d,]+)\s*条/, toZh: /([\d,]+)\s*results/, en: "$1 results", zh: "共 $1 条" },
    { toEn: /已选\s*(\d+)\s*个对话/, toZh: /(\d+)\s*selected/, en: "$1 selected", zh: "已选 $1 个对话" },
    { toEn: /证据片段（(\d+)）/, toZh: /Evidence \((\d+)\)/, en: "Evidence ($1)", zh: "证据片段（$1）" },
    { toEn: /工具轨迹（(\d+)\s*步）/, toZh: /Tool trace \((\d+) steps\)/, en: "Tool trace ($1 steps)", zh: "工具轨迹（$1 步）" },
    { toEn: /第\s*(\d+)\s*\/\s*(\d+)\s*页/, toZh: /Page (\d+) \/ (\d+)/, en: "Page $1 / $2", zh: "第 $1 / $2 页" },
    // Ordered specific-first. NOTE: do not use \b after CJK characters — it never matches
    // (CJK are non-word chars for \w), so phrases ending in Chinese must not carry \b.
    { toEn: /查看\s*(\d+)\s*条原始记录/, toZh: /View (\d+) source records/, en: "View $1 source records", zh: "查看 $1 条原始记录" },
    { toEn: /([\d,]+)\s*条记录已建立关系/, toZh: /([\d,]+) records linked as relations/, en: "$1 records linked as relations", zh: "$1 条记录已建立关系" },
    { toEn: /([\d,]+)\s*个已归并实体/, toZh: /([\d,]+) merged entities/, en: "$1 merged entities", zh: "$1 个已归并实体" },
    { toEn: /(\d+)\s*个Conference\s*·\s*(\d+)\s*个Journal/, toZh: /(\d+) conferences · (\d+) journals/, en: "$1 conferences · $2 journals", zh: "$1 个Conference · $2 个Journal" },
    { toEn: /([\d.]+)%\s*的全量实体命中至少一个主题/, toZh: /([\d.]+)% of all entities match at least one topic/, en: "$1% of all entities match at least one topic", zh: "$1% 的全量实体命中至少一个主题" },
    { toEn: /全库\s*([\d.]+)%\s*·\s*已分类\s*([\d.]+)%/, toZh: /All\s*([\d.]+)% · classified\s*([\d.]+)%/, en: "All $1% · classified $2%", zh: "全库 $1% · 已分类 $2%" },
    { toEn: /已分类\s*([\d,]+)（([\d.]+)%）\s*·\s*多标签率\s*([\d.]+)%\s*·\s*(.*)$/, toZh: /Classified\s*([\d,]+) \(([\d.]+)%\)\s*·\s*multi-label rate\s*([\d.]+)%\s*·\s*(.*)$/, en: "Classified $1 ($2%) · multi-label rate $3% · $4", zh: "已分类 $1（$2%）· 多标签率 $3% · $4" },
    { toEn: /\+\s*(\d+)\s*类/, toZh: /\+\s*(\d+) more/, en: "+$1 more", zh: "+$1 类" },
    { toEn: /([\d,]+)\s*个文件/, toZh: /([\d,]+) files/, en: "$1 files", zh: "$1 个文件" },
    { toEn: /([\d,]+)\s*条记录/, toZh: /([\d,]+) records/, en: "$1 records", zh: "$1 条记录" },
    { toEn: /共\s*([\d,]+)\s*个实体/, toZh: /([\d,]+) entities/, en: "$1 entities", zh: "共 $1 个实体" },
    { toEn: /([\d,]+)\s*个实体/, toZh: /([\d,]+) entities/, en: "$1 entities", zh: "$1 个实体" },
    { toEn: /共\s*([\d,]+)\s*组/, toZh: /([\d,]+) groups/, en: "$1 groups", zh: "共 $1 组" },
    { toEn: /共\s*([\d,]+)\s*个集合/, toZh: /([\d,]+) collections/, en: "$1 collections", zh: "共 $1 个集合" },
    // hybrid sentences that embed English tokens inside Chinese (tolerant of optional spaces)
    { toEn: /([\d,]+)\s*条已有\s*Abstract/, toZh: /([\d,]+) with abstracts/, en: "$1 with abstracts", zh: "$1 条已有Abstract" },
    { toEn: /原始记录不\s*Delete、不\s*覆盖/, toZh: /Source records are never deleted or overwritten/, en: "Source records are never deleted or overwritten", zh: "原始记录不Delete、不覆盖" },
    { toEn: /不\s*OK\s*关系不自动归并/, toZh: /Non-OK relations are not auto-merged/, en: "Non-OK relations are not auto-merged", zh: "不OK关系不自动归并" },
    { toEn: /同一[\s\S]{0,8}?符对应的题名或Author不一致，禁止自动归并/, toZh: /Same ID but mismatched title\/authors — auto-merge disabled/, en: "Same ID but mismatched title/authors — auto-merge disabled", zh: "同一标识符对应的题名或Author不一致，禁止自动归并" },
    { toEn: /已知重复出版或需解释\s*Relation/, toZh: /Known duplicates or relations needing explanation/, en: "Known duplicates or relations needing explanation", zh: "已知重复出版或需解释Relation" },
    { toEn: /([\d,]+)\s*条已有摘要/, toZh: /([\d,]+) with abstracts/, en: "$1 with abstracts", zh: "$1 条已有摘要" },
    { toEn: /尚待\s*([\d,]+)\s*条/, toZh: /([\d,]+) pending/, en: "$1 pending", zh: "尚待 $1 条" },
    { toEn: /(\d+)\s*个会议\s*·\s*(\d+)\s*个期刊/, toZh: /(\d+) conferences · (\d+) journals/, en: "$1 conferences · $2 journals", zh: "$1 个会议 · $2 个期刊" },
    { toEn: /([\d,]+)\s*个\s*venue\s*·\s*会议\s*(\d+)\s*·\s*期刊\s*(\d+)/, toZh: /([\d,]+) venues · (\d+) conferences · (\d+) journals/, en: "$1 venues · $2 conferences · $3 journals", zh: "$1 个venue · 会议 $2 · 期刊 $3" },
    { toEn: /共\s*([\d,]+)\s*篇/, toZh: /([\d,]+) papers/, en: "$1 papers", zh: "共 $1 篇" },
    { toEn: /([\d,]+)\s*最终\s*·\s*([\d,]+)\s*滚动/, toZh: /([\d,]+) final · ([\d,]+) rolling/, en: "$1 final · $2 rolling", zh: "$1 最终 · $2 滚动" },
    { toEn: /(\d+)\s*个阻塞问题/, toZh: /(\d+) blocking issues/, en: "$1 blocking issues", zh: "$1 个阻塞问题" },
  ];

  // Content regions that must never be auto-translated.
  const CONTENT_SKIP = [
    ".chat-md", ".chat-thinking", ".chat-msg-user", ".chat-msg-content", ".chat-error",
    ".chat-evidence-item", ".chat-evidence-list", ".chat-trace-list",
    ".abstract-details p", ".collection-name", ".chat-item-title",
    "button.collection-chip", "button.collection-open", ".paper-title",
    "#research-editor", ".research-record strong", "#chat-research-scope", ".collection-nav", "#my-library-title", "#picker-existing", "#picker-papers", "#picker-feedback",
  ].join(", ");

  function isContent(el) {
    while (el && el !== document.body) {
      if (el.matches && el.matches(CONTENT_SKIP)) return true;
      el = el.parentElement;
    }
    return false;
  }

  const translatedText = new WeakMap();
  function translateTextNode(node, toEn) {
    const raw = node.nodeValue || "";
    if (!raw) return;
    if (!toEn) {
      const previous = translatedText.get(node);
      if (previous && raw === previous.en) node.nodeValue = previous.zh;
      translatedText.delete(node);
      return;
    }
    if (toEn && !CJK.test(raw)) return; // already-English node
    if (!toEn && !/[A-Za-z]/.test(raw)) return; // already-Chinese node
    let out = raw;
    // Composite/numeric phrases first (they may contain words that the generic dictionary
    // would otherwise translate piecemeal and break, e.g. "…记录已建立关系").
    for (const p of PATTERNS) {
      const re = toEn ? p.toEn : p.toZh;
      const fmt = toEn ? p.en : p.zh;
      if (re.test(out)) out = out.replace(re, fmt);
    }
    // Then the longest-match vocabulary (individual labels/names inside longer text).
    out = toEn ? out.replace(EN_RE, (m) => CHROME[m]) : out.replace(ZH_RE, (m) => REVERSE[m]);
    if (out !== raw) { translatedText.set(node, {zh: raw, en: out}); node.nodeValue = out; }
  }

  function walkText(root, toEn) {
    if (!root) return;
    if (root.nodeType === Node.TEXT_NODE) {
      if (!isContent(root.parentElement)) translateTextNode(root, toEn);
      return;
    }
    if (root.matches && isContent(root)) return;
    const child = root.childNodes;
    for (let i = 0; i < child.length; i += 1) {
      const n = child[i];
      if (n.nodeType === Node.TEXT_NODE) {
        if (!isContent(n.parentElement)) translateTextNode(n, toEn);
      } else if (n.nodeType === Node.ELEMENT_NODE) {
        walkText(n, toEn);
      }
    }
  }

  let translating = false;
  let observer = null;

  function startObserver() {
    if (observer) return;
    observer = new MutationObserver((records) => {
      if (translating || current() !== "en") return;
      translating = true;
      try {
        for (const rec of records) {
          if (rec.type === "characterData" && rec.target.nodeType === Node.TEXT_NODE) {
            // text written later via textContent (counts like "共 N 条", dates, …)
            if (!isContent(rec.target.parentElement)) translateTextNode(rec.target, true);
          } else if (rec.type === "childList") {
            for (const added of rec.addedNodes) {
              if (added.nodeType === Node.ELEMENT_NODE) {
                walkText(added, true);
              } else if (added.nodeType === Node.TEXT_NODE && !isContent(added.parentElement)) {
                translateTextNode(added, true);
              }
            }
          }
        }
      } finally {
        translating = false;
      }
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  function stopObserver() {
    if (observer) {
      observer.disconnect();
      observer = null;
    }
  }

  function translateDynamic(locale) {
    translating = true;
    try {
      walkText(document.body, locale === "en");
    } finally {
      translating = false;
    }
    if (locale === "en") startObserver();
    else stopObserver();
  }

  function setLocale(locale, { notify = true } = {}) {
    const next = SUPPORTED.includes(locale) ? locale : "zh";
    try {
      localStorage.setItem(KEY, next);
    } catch {
      /* storage unavailable; keep for this session */
    }
    document.documentElement.lang = next === "zh" ? "zh-CN" : "en";
    applyStatic(next);
    translateDynamic(next);
    if (notify) {
      document.dispatchEvent(new CustomEvent("apex:langchange", { bubbles: true, detail: { lang: next } }));
    }
  }

  function renderToggle() {
    const toggle = document.getElementById("lang-toggle");
    if (!toggle) return;
    const active = current();
    toggle.querySelectorAll("[data-lang]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.lang === active);
    });
  }

  function bind() {
    applyStatic(current());
    renderToggle();
    translateDynamic(current());
    const toggle = document.getElementById("lang-toggle");
    if (toggle) {
      toggle.addEventListener("click", (event) => {
        const btn = event.target.closest("[data-lang]");
        if (!btn) return;
        setLocale(btn.dataset.lang);
        renderToggle();
      });
    }
  }

  window.I18N = {
    lang: current,
    setLocale,
    applyStatic,
    translateDynamic,
    renderToggle,
    isEn() {
      return current() === "en";
    },
    CHROME,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
