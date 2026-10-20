// Display-only translations for known platform-owned domain labels.
// Callers must distinguish built-ins from user-authored labels/descriptions.
// Never apply this catalog to IDs, persisted values, question text, or answers.
// Chinese keys are intentional: they match the original labels verbatim.

const topicVocabulary: Record<string, string> = {
  "数论": "Number theory",
  "计算与运算": "Arithmetic and operations",
  "基础口算": "Mental arithmetic",
  "四则混合运算": "Mixed arithmetic operations",
  "巧算与速算": "Efficient calculation",
  "数字与数位": "Digits and place value",
  "数字谜与算式谜": "Digit and arithmetic puzzles",
  "整除特征": "Divisibility rules",
  "质数与合数": "Prime and composite numbers",
  "最大公因数与最小公倍数": "Greatest common divisor and least common multiple",
  "余数与同余": "Remainders and congruences",
  "奇偶性": "Parity",
  "分数": "Fractions",
  "小数": "Decimals",
  "百分数": "Percentages",
  "整数": "Integers",
  "应用题": "Word problems",
  "分数应用题": "Fraction word problems",
  "比例应用题": "Proportion word problems",
  "比和比例": "Ratios and proportions",
  "比例关系": "Proportional relationships",
  "数量关系建模": "Modeling quantitative relationships",
  "行程": "Motion problems",
  "行程问题": "Motion problems",
  "基本行程问题": "Basic motion problems",
  "相遇问题": "Meeting problems",
  "追及问题": "Catch-up problems",
  "工程": "Work-rate problems",
  "工程问题": "Work-rate problems",
  "经济问题": "Money and financial word problems",
  "年龄问题": "Age problems",
  "植树问题": "Tree-planting problems",
  "和差问题": "Sum-and-difference problems",
  "和倍问题": "Sum-and-ratio problems",
  "差倍问题": "Difference-and-ratio problems",
  "盈亏问题": "Surplus-and-shortage problems",
  "鸡兔同笼": "Chickens-and-rabbits problems",
  "几何": "Geometry",
  "几何图形": "Geometric figures",
  "平面几何": "Plane geometry",
  "立体几何": "Solid geometry",
  "立体图形": "Three-dimensional shapes",
  "面积": "Area",
  "周长": "Perimeter",
  "体积": "Volume",
  "代数与规律": "Algebra and patterns",
  "数列与规律": "Sequences and patterns",
  "周期规律": "Periodic patterns",
  "方程与代数式": "Equations and algebraic expressions",
  "一元一次方程": "Linear equations in one variable",
  "方程组与消元思想": "Systems of equations and elimination",
  "不等式与取值范围": "Inequalities and value ranges",
  "对应关系与函数思想": "Correspondences and functional thinking",
  "二元关系": "Binary relations",
  "计数": "Counting",
  "组合": "Combinatorics",
  "计数与组合": "Counting and combinatorics",
  "计数、组合与概率": "Counting, combinatorics, and probability",
  "分类枚举": "Enumeration by cases",
  "逻辑与策略": "Logic and strategy",
  "真假话": "Truth-and-lie puzzles",
  "抽屉原理": "Pigeonhole principle",
  "不变量与操作问题": "Invariants and operation problems",
  "最值与构造": "Extrema and constructions",
  "综合与创新": "Integrated and creative problems",
  "未分类": "Unclassified",
};

const methodVocabulary: Record<string, string> = {
  "不变量法": "Invariant method",
  "份数法": "Equal-parts method",
  "倒推法": "Working backward",
  "分段讨论": "Piecewise analysis",
  "分类计数": "Counting by cases",
  "分类讨论": "Case analysis",
  "列方程": "Forming equations",
  "列表推理": "Tabular reasoning",
  "反证法": "Proof by contradiction",
  "同余法": "Congruence method",
  "周期法": "Periodicity method",
  "奇偶分析": "Parity analysis",
  "容斥原理": "Inclusion-exclusion principle",
  "对应量分析": "Analysis of corresponding quantities",
  "总量守恒": "Conservation of totals",
  "抽屉原理": "Pigeonhole principle",
  "排除法": "Eliminating possibilities",
  "数形结合": "Combining algebraic and geometric reasoning",
  "整体法": "Whole-expression method",
  "整除特征": "Divisibility rules",
  "极端法": "Extremal principle",
  "构造法": "Constructive method",
  "枚举法": "Enumeration",
  "比例法": "Proportional reasoning",
  "消元法": "Elimination method",
  "相对速度": "Relative speed",
  "约分": "Reducing fractions",
  "质因数分解": "Prime factorization",
  "运算顺序": "Order of operations",
  "递推计数": "Recursive counting",
  "画图法": "Drawing diagrams",
  "线段图": "Bar models",
  "假设法": "Assumption method",
  "转化法": "Transformation method",
  "代入法": "Substitution method",
  "验证法": "Verification method",
  "枚举": "Enumeration",
  "分类": "Classification",
  "转化": "Transformation",
  "倒推": "Working backward",
};

const taskVocabulary: Record<string, string> = {
  "判断": "Decide",
  "判断正误": "Determine true or false",
  "反推原量": "Recover the original quantity",
  "方案设计": "Design a plan",
  "构造": "Construct",
  "求个数": "Find the count",
  "求人数": "Find the number of people",
  "求值": "Evaluate",
  "求最大值": "Find the maximum",
  "求最小值": "Find the minimum",
  "求取值范围": "Find the range of values",
  "求和": "Find the sum",
  "求总量": "Find the total quantity",
  "求数量": "Find the quantity",
  "求时间": "Find the time",
  "求未知数": "Find the unknown",
  "求种数": "Count the possibilities",
  "求距离": "Find the distance",
  "求面积": "Find the area",
  "求周长": "Find the perimeter",
  "求体积": "Find the volume",
  "求速度": "Find the speed",
  "求比例": "Find the ratio",
  "计算": "Calculate",
  "证明": "Prove",
  "选择答案": "Choose an answer",
  "比较": "Compare",
  "计数": "Counting",
};

const structureVocabulary: Record<string, string> = {
  "几何题": "Geometry problem",
  "图形题": "Diagram-based problem",
  "填空题": "Fill-in-the-blank problem",
  "多步骤题": "Multi-step problem",
  "多问综合题": "Integrated multi-part problem",
  "开放题": "Open-ended problem",
  "操作题": "Hands-on problem",
  "数位题": "Place-value problem",
  "数论题": "Number-theory problem",
  "文字应用题": "Word problem",
  "新定义题": "New-definition problem",
  "立体图形题": "Three-dimensional geometry problem",
  "纯计算式": "Arithmetic expression",
  "行程应用题": "Motion word problem",
  "证明题": "Proof problem",
  "选择题": "Multiple-choice problem",
  "逻辑推理题": "Logical-reasoning problem",
  "多条件": "Multiple conditions",
  "图形组合": "Composite figures",
  "递推结构": "Recursive structure",
};

const constraintVocabulary: Record<string, string> = {
  "不允许重复": "No repetition allowed",
  "各不相同": "All distinct",
  "恰好": "Exactly",
  "整除条件": "Divisibility condition",
  "正整数": "Positive integers",
  "相邻限制": "Adjacency restriction",
  "自然数": "Natural numbers",
  "至多": "At most",
  "至少": "At least",
  "范围限制": "Range restriction",
  "顺序限制": "Ordering restriction",
  "非负整数": "Nonnegative integers",
  "互不相同": "Pairwise distinct",
  "允许重复": "Repetition allowed",
  "不用方程": "Without equations",
  "必须可判分": "Must be automatically gradable",
  "唯一解": "Unique solution",
  "余数不变": "Unchanged remainder",
  "余数条件": "Remainder condition",
  "体积变化": "Volume change",
  "体积相同": "Equal volumes",
  "比例尺": "Scale",
};

const sourceEnglishCatalog: Record<string, string> = {
  "AI生成题": "AI-generated questions",
  "俄罗斯·春季阿基米德竞赛": "Russia: Spring Archimedes Competition",
  "俄罗斯·斯皮瓦克": "Russia: Spivak Collection",
  "俄罗斯·俄罗斯全国奥赛学校阶段": "Russia: All-Russian Olympiad, School Round",
  "俄罗斯·莫斯科数学节六年级": "Russia: Moscow Mathematical Festival, Grade 6",
  "春季阿基米德竞赛": "Spring Archimedes Competition",
  "斯皮瓦克": "Spivak Collection",
  "俄罗斯全国奥赛学校阶段": "All-Russian Olympiad, School Round",
  "莫斯科数学节六年级": "Moscow Mathematical Festival, Grade 6",
  "俄罗斯·俄罗斯题目": "Russia: Mathematics Problems",
  "俄罗斯题目": "Russian Mathematics Problems",
  "春季阿基米德竞赛.json": "Russia: Spring Archimedes Competition",
  "斯皮瓦克.json": "Russia: Spivak Collection",
  "vmo_grade5_6_text_only_question_v2.json": "Russia: All-Russian Olympiad, School Round",
  "matprazdnik_question_v2_1990_2026.json": "Russia: Moscow Mathematical Festival, Grade 6",
  "丘班真题": "Qiu Class Past Papers",
  "华罗庚杯": "Hua Luogeng Cup",
  "希望杯": "Hope Cup",
  "华附小联盟大联盟": "Huafu / Small Alliance / Big Alliance",
  "ai_generated_gpt_6packs_20260721_import.json": "AI-generated questions",
};

// Exact bundled filename aliases and the compact labels seen in source options.
// This expands only these fixed strings, never arbitrary filenames or user text.
const bundledSources: ReadonlyArray<readonly [string, string]> = [
  ["丘班真题_纯文字题目整理(1)", "Qiu Class Past Papers"],
  ["华罗庚杯1986-2020_纯文字题目整理", "Hua Luogeng Cup (1986-2020)"],
  ["希望杯五年级_文字题整理_2010-2017", "Hope Cup, Grade 5 (2010-2017)"],
  ["希望杯六年级_2010-2017_纯文字题整理", "Hope Cup, Grade 6 (2010-2017)"],
  ["数学文字题整理_华附小联盟大联盟_2006-2014", "Huafu / Small Alliance / Big Alliance (2006-2014)"],
];
for (const [source, english] of bundledSources) {
  for (const suffix of ["", ".json", "_手工标签分类", "_手工标签分类.json"]) {
    sourceEnglishCatalog[source + suffix] = english;
  }
}

function prefixAliases(prefix: string, vocabulary: Record<string, string>): Record<string, string> {
  const aliases: Record<string, string> = {};
  for (const [label, english] of Object.entries(vocabulary)) {
    aliases[`${prefix}:${label}`] = `${prefix}:${english}`;
    aliases[`${prefix}：${label}`] = `${prefix}:${english}`;
  }
  return aliases;
}

export const domainEnglishCatalog: Record<string, string> = {
  ...sourceEnglishCatalog,
  ...topicVocabulary,
  ...methodVocabulary,
  ...taskVocabulary,
  ...structureVocabulary,
  ...constraintVocabulary,
  ...prefixAliases("T", taskVocabulary),
  ...prefixAliases("S", structureVocabulary),
  ...prefixAliases("M", methodVocabulary),
  ...prefixAliases("C", constraintVocabulary),
  "默认学习者": "Default learner",
  "默认学员": "Default learner",
  "主知识线": "Primary knowledge",
  "辅助知识线": "Supporting knowledge",
  "解题方法": "Solution methods",
  "任务类型": "Task type",
  "题目结构": "Question structure",
  "约束条件": "Constraints",
  "限制条件": "Constraints",
  "自定义标签": "Custom tags",
  "题目最核心的知识领域，用来决定出题主方向。": "The core knowledge area that determines the main direction of question generation.",
  "会一起出现的次要知识点，用来做交叉综合。": "Supporting knowledge points combined with the main topic for integrated practice.",
  "希望学生使用或训练的方法，例如枚举、分类、转化。": "Methods for the learner to use or practice, such as enumeration, classification, and transformation.",
  "题目要求完成的动作，例如求值、证明、计数、比较。": "The action the question requires, such as evaluating, proving, counting, or comparing.",
  "题面组织方式，例如多条件、图形组合、递推结构。": "How the problem is organized, such as multiple conditions, composite figures, or a recursive structure.",
  "出题时必须遵守或避免的条件，例如不用方程、必须可判分。": "Conditions to follow or avoid when generating questions, such as using no equations or requiring automatic grading.",
  "自定义或导入数据中的标签类型。": "A tag category from custom or imported data.",
};

const generatedModeLabels: Record<string, string> = {
  "普通练习": "Regular practice",
  "严格测试": "Strict test",
  "套题练习": "Set practice",
  "历史重做": "History review",
  "标准诊断": "Standard diagnosis",
  "批量类似题": "Similar-question batch",
  "套题出题": "Question-set generation",
};

// Anchored, closed-vocabulary patterns corresponding to backend title builders.
// There is no wildcard translation of a title's free-text portion.
const calendarDay = "(?:19|20)\\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\\d|3[01])";
const compactTime = "(?:(?:19|20)\\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\\d|3[01])-(?:[01]\\d|2[0-3])[0-5]\\d|no-time)";
const shortId = "#[0-9a-fA-F]{8}";
const historyTitlePattern = new RegExp(
  `^(${calendarDay}) (普通练习|严格测试|套题练习|历史重做|标准诊断|批量类似题|套题出题) (\\d{1,6})题$`,
);
const sessionTitlePattern = new RegExp(
  `^(普通练习|严格测试|套题练习|历史重做|标准诊断) · ([^\\r\\n]+) · (${compactTime}) · (\\d{1,6})题 · (${shortId})$`,
);
const generatedBatchPattern = new RegExp(
  `^AI出题 · ([1-5]星|不限星级) · (\\d{1,6}标签|不限标签) · (${compactTime}) · (\\d{1,6})/(\\d{1,6})题 · (${shortId})$`,
);
const variantBatchPattern = new RegExp(
  `^AI类似题 · (跨历史选题|\\d{1,6}原题x\\d{1,6}) · (${compactTime}) · (\\d{1,6})/(\\d{1,6})题 · (${shortId})$`,
);

function knownSource(value: string): string | null {
  return Object.prototype.hasOwnProperty.call(sourceEnglishCatalog, value)
    ? sourceEnglishCatalog[value]
    : null;
}

function generatedOrigin(mode: string, origin: string): string | null {
  if (mode === "套题练习") {
    if (origin === "批量类似题") return "Similar-question batch";
    if (origin === "套题同步") return "Synchronized question set";
    const batch = /^(类似题批次|套题批次) ([0-9a-fA-F]{8})$/.exec(origin);
    return batch ? `${batch[1] === "类似题批次" ? "Similar-question batch" : "Question-set batch"} ${batch[2]}` : null;
  }
  if (mode === "严格测试") return origin === "严格测试" ? "Strict test" : null;
  if (mode === "历史重做") {
    if (origin === "历史记录") return "History record";
    const history = historyTitlePattern.exec(origin);
    return history ? `${history[1]} ${generatedModeLabels[history[2]]} ${history[3]} questions` : null;
  }
  if (origin === "题库抽题") return "Question-bank selection";
  const stars = /^([1-5](?:,[1-5]){0,4})星$/.exec(origin);
  if (stars) return `${stars[1]} stars`;
  const tags = /^标签 (\d{1,6})个$/.exec(origin);
  if (tags) return `${tags[1]} tags`;
  if (origin.startsWith("题源 ")) {
    const sources = origin.slice(3).split("、");
    if (sources.length < 1 || sources.length > 2) return null;
    const labels = sources.map(knownSource);
    return labels.every((label) => label !== null) ? `Source: ${labels.join(", ")}` : null;
  }
  return null;
}

/**
 * Display-only formatting for known, system-owned title fields.
 * Unknown, truncated, multiline, or custom-origin titles are returned verbatim.
 * A string alone cannot prove provenance: callers must not pass arbitrary user
 * titles, even if a user happens to name one exactly like a generated title.
 * No locale module is imported, so language.ts can safely import this helper.
 */
export function displayAutoTitle(value: string, language: "en" | "zh-CN"): string {
  if (language !== "en" || value.length > 512 || /[\r\n]/.test(value)) return value;

  const history = historyTitlePattern.exec(value);
  if (history) return `${history[1]} ${generatedModeLabels[history[2]]} ${history[3]} questions`;

  const session = sessionTitlePattern.exec(value);
  if (session) {
    const origin = generatedOrigin(session[1], session[2]);
    if (origin === null) return value;
    return `${generatedModeLabels[session[1]]} · ${origin} · ${session[3]} · ${session[4]} questions · ${session[5]}`;
  }

  const batch = generatedBatchPattern.exec(value);
  if (batch) {
    const stars = batch[1] === "不限星级" ? "Any difficulty" : `${batch[1].slice(0, -1)} stars`;
    const tags = batch[2] === "不限标签" ? "Any tags" : `${batch[2].slice(0, -2)} tags`;
    return `AI generation · ${stars} · ${tags} · ${batch[3]} · ${batch[4]}/${batch[5]} questions · ${batch[6]}`;
  }

  const variant = variantBatchPattern.exec(value);
  if (variant) {
    const counts = /^(\d{1,6})原题x(\d{1,6})$/.exec(variant[1]);
    const source = counts ? `${counts[1]} source questions x ${counts[2]}` : "Selected from history";
    return `AI similar questions · ${source} · ${variant[2]} · ${variant[3]}/${variant[4]} questions · ${variant[5]}`;
  }
  return value;
}
