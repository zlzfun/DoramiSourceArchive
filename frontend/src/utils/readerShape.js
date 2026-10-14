// 阅读器容器(呈现形态):文章 / 播客 / 社交。issue #178 撤销「动态」容器并入文章;
// 后端已不再下发 bulletin,这里仍把旧值折叠到 article,防止旧数据或旧接口把源分到不存在的容器。
export const READER_SHAPES = Object.freeze(['article', 'podcast', 'social']);

export function readerShapeOf(sourceOrShape) {
  const raw = typeof sourceOrShape === 'string' ? sourceOrShape : sourceOrShape?.shape;
  const shape = String(raw || '').trim().toLowerCase();
  return READER_SHAPES.includes(shape) ? shape : 'article';
}
