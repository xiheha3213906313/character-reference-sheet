# 三选一记录接口

只在常规完整制作的production验收时读取。流程和观察标准见 [三选一](quality-and-revision.md#合格视图三选一)。记录JSON的 `schema_version:1`，`stages` 按 `head/front/back/left` 保存各阶段；上游检查允许后续视图尚未完成。所有路径相对验收项目根目录，recipe的原始输入也可用实际绝对路径。

每个阶段包含：

- `recipe`：`prompt_file`、`prompt_sha256`、`inputs`（有序数组，每项含file、sha256、purpose）、`tool`、`parameters`（对象）。完整提示词保存为非空文本文件，哈希按文件原始字节计算；参数如实记录可控值。
- `calls`：恰好三项，按实际先后排列；每项的 `id` 非空且不重复，复制共同配置的prompt_sha256、inputs、tool、parameters，并指向真实 `evidence_file` / `evidence_sha256` 调用证据。证据可以是保存的真实请求/结果记录或日志，不补造缺失的历史调用。
- 用户明确限制数量时，阶段可写 `required_calls:1` 或2并填写 `user_override_reason` 保存真实要求，此时calls恰好为该数量，其他检查不变；未写时默认三次。不能自行用数量例外绕过常规三选一。
- 每次调用的 `output` 指向完整候选PNG或已归档候选，并包含其sha256；`checks` 为本阶段所有固定目标id到pass/fail/pending的映射，`result` 只有全部pass才是pass，否则为fail，`observation` 保存实际观察。首张及所选候选必须pass；后两张允许fail，不强制补生成。
- `selected_call` 指向实际所选调用id，`selection_reason` 写具体选择依据。
- `processing` 可省略或为空；选定后抠像、导出或已授权返工造成哈希改变时，每步含input_sha256、output_sha256、record_file、record_sha256。真实旁录也需含input_sha256（或source_sha256）和output_sha256，前一步输出接下一步输入，最终输出必须等于当前验收PNG哈希。候选及旁录随制作记录保留。

输出引用二选一：

```json
{"file":"候选/000001_特写.png","sha256":"实际候选哈希"}
```

交付时默认候选已在画布数据中，使用：

```json
{"canvas_manifest":"网页资源/画布清单.json","candidate_id":"head-实际候选哈希","sha256":"实际候选哈希"}
```

画布清单路径可带套图子目录，candidate_id须对应此视图，左侧面在网页中使用side。核验器读取对应完整图片数据，不把仅存在的账本条目当作图片有效。

当前 `验收状态.json` 用selection_file及selection_sha256绑定整份选择记录。增加后续阶段或修改记录后更新此哈希；图片、提示词、输入、调用证据或后处理记录变化时，复检受影响内容后再更新。

既有图片评审在底稿明确task_mode为existing_review，完整交付清单也保持同一模式；不要求不存在的生成历史。单张任务不强制完整套三选一。未验收打包和仅生成试跑仅检查文件，不使用批准门槛。记录完整和版本一致仍不能证明执行者实际进行了调用或正确看图。
