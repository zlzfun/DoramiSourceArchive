// 推文正文的折叠(issue #346)。
//
// 两个判定各管各的,不能合成一个:
//   · 字数只决定「这条要不要进折叠态」——短推文不值得为它量版面;
//   · 「显示更多」出不出现,看折叠态是否真的截掉了内容。能排几行取决于栏宽、字号和正文里的换行,
//     字数说明不了:桌面栏宽下 400 多字的英文推文常常排不满 7 行,按字数出按钮就成了点了没反应的空按钮。

export const SOCIAL_CLAMP_CHARS = 360;

/** 这条正文够不够长到进折叠态(只是候选,不代表真的会被截)。 */
export const socialClampCandidate = (text) => (text || '').length > SOCIAL_CLAMP_CHARS;

/** 限高的盒子是否截掉了内容:内容高超过可见高(留 1px 给小数行高的取整)。 */
export const clampCuts = (box) => box.scrollHeight - box.clientHeight > 1;

/** 「显示更多 / 收起」出不出现:展开之后一直在(得能收回去),折叠时只在确实截掉了内容时出现。 */
export const socialMoreVisible = ({ candidate, expanded, cut }) => Boolean(candidate && (expanded || cut));
