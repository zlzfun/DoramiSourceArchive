// 兴趣保存的模块级串行队列(自 InterestPage 抽出):整套替换语义的 PUT 必须按发出顺序落库,
// 组件卸载后排入的「最后一次合并保存」也在同一条链上。
// flushPendingInterestSaves:等本标签页已排队的保存全部落定(issue #56 codex 检视——早报页
// 「稍后再说」若抢在兴趣页卸载时排入的 PUT 之前完成引导,库里还是零兴趣不会重编,随后的 PUT
// 又按 v3.51.1 不重编,首次兴趣重编就此永久错过)。
let saveChain = Promise.resolve();
export const enqueueSave = (task) => {
  const run = saveChain.then(task);
  saveChain = run.then(() => undefined, () => undefined);
  return run;
};
export const flushPendingInterestSaves = () => saveChain;
