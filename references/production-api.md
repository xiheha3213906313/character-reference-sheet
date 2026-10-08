# 制作接口

普通新制作只调用本接口，整个制作和交付过程都不读脚本、模板或网页源码。低层源码仅用于用户明确要求的技能维护/源码调试。交付时也只调用deliver并读对应接口说明，不研究实现；不提前读交付文档，不现场编写裁图、哈希、归档、排版或复制脚本。输入错误按返回字段修正；脚本自身异常使用已文档化恢复方式，无法恢复则报告具体故障，不自行转入维护。

Windows入口：`& "<技能目录>/scripts/sheet_flow.ps1" <命令> --root "<工作目录>" ...`。其他系统用可用Python运行 `scripts/sheet_flow.py`，其参数相同。入口自动选择已提供的运行时，不反复试失效的默认python。所有JSON文件为UTF-8；相对图片路径按root解析；config本身按命令当前目录解析。阶段输入的真实坐标按各自原图像素填写。

每次调用都返回`next_action`及下一步所需参数/文件，直接执行下一步，不另跑status、重新列目录或重读提示词。仅恢复被中断的任务时用status，它只读、不新增调用。没有版本变化或具体未解决疑点时，不重复打开已清楚的同一文档或图片；独立复核者须首次亲自看必要证据。模型实际调用图像工具，脚本不调用模型、不判断视觉。上述访问规则约束执行行为，不是操作系统层面的读文件权限封锁。

| 当前动作 | 命令 | 模型只提供 | 脚本自动完成 |
|---|---|---|---|
| 开始 | `start --sources "<素材目录>"` | 素材目录 | 清点当前目录图片，编号、尺寸、哈希、快照、首次准备表 |
| 准备当前阶段 | `prepare --stage head --config "<输入.json>"` | 一次角色/素材分析及本阶段语义 | 固定模板编译、实际输入容量检查、验收目标、配方、请求参数 |
| 工具返回 | `receive --stage head --config "<结果.json>"` | 本批真实request_id、tool_call_id、输出或失败 | 一次归档、调用次数、证据、拼图、记录 |
| 制作端简评 | `self-check --stage head --config "<简评.json>"` | 拼图id、简短实际观察、pass/fail | 绑定候选与版本；通过才开放代理交接 |
| 子代理报告 | `review --stage head --config "<代理直接写的报告.json>"` | 代理实际观察/结论 | 字段预检、像素采样、固定记录、下一步；不重抄报告 |
| 首次待核实 | `supplement --stage head --config "<补证.json>"` | 疑点及实际裁切坐标 | 一次补证局部/对照、pending报告模板、原代理交接 |
| 三选一或上限选优 | `rank --stage head --config "<选择.json>"` | 拼图版本、所选编号、一句理由 | 排序记录、选定或回退、必要复核入口 |
| 四阶段完成 | `deliver`，实际看图后再`deliver --config "<交付简查.json>"` | 两项简查观察；用户另定位置时提供原话 | 四PNG、全部候选数据、现成网页、总览、角色描述、清单、复制与最终核对 |
| 恢复 | `status --stage head` | 当前阶段 | 返回已经准备好的下一步；不生成、不重验、不写记录 |

阶段键固定`head/front/back/left`，依赖顺序固定特写→正面→背面→左侧面。接口内部完成网页的left→side映射。

## prepare

开始只读选材规范，实际查看每张原图和确有必要的细节。观察与采用决定一次写入start生成的`制作记录/阶段输入/首次准备.json`。先分析内容，再决定采用；同一文件即可，不另写Markdown。脚本会自动生成图片选用文档和角色描述。

首次输入示例（文字须换成本任务真实观察；不填写哈希/证据标签/依赖）：

```json
{
  "character": {
    "name": "角色名称", "height_cm": 163, "age_years": 19,
    "appearance": "依据本任务观察写实际外貌",
    "outfit": "本套服饰及材料", "outfit_name": "描述性造型后缀",
    "setting_basis": "身高年龄沿用来源或本次估计依据"
  },
  "materials": [
    {"source_id":"s001","views":["front"],"observation":"本图全图内容、可见身份与服饰的实际观察","quality":"分别记录脸、衣料、图案的清晰度、遮挡和污染","decision":"adopt","selection_reason":"与其他图比较后保留的互补依据","uses":[{"id":"face_identity","kind":"identity","stages":["head"],"target":"本图可辨的实际脸型与五官关系"}]}
  ],
  "prompt": {
    "operation":"generate", "identity":"本角色实际身份特征",
    "references":[],
    "critical_constraints":[]
  }
}
```

`materials`必须覆盖全部来源，`decision`固定`adopt/partial/exclude`；`views`为实际视角的不重复列表，固定`front/back/side/rear_oblique/detail/unclear`，局部看得出背面仍标back或rear_oblique，不能只写detail掩盖视角。`observation`记录图中实际内容；`quality`单独记录分区清晰度、模糊、遮挡和可用边界；全部观察完成后填`selection_reason`说明与其他素材的比较依据，再决定采用。不能只写“已看”。`uses`为固定对象列表，每项必须有全任务唯一英文`id`、`kind:identity/design/material`、`stages:[head/front/back/left]`、`target`，必要时加本张原图像素`crop:[x0,y0,x1,y1]`。采用项须有uses，exclude须为空；不接受自由字符串用途。同部位优先清晰且互补的图，不能按整图像素大小自动判清晰。完全重复且更模糊的排除图可选填covered_by:{"source_id":"s002","reason":"清晰图如何覆盖本图全部相关信息"}，脚本据此替换残留手写引用；含独有必需细节的图保留partial及对应uses，不能填covered_by。脚本不把视角标签或非空文字当作视觉正确证明。身高年龄按主文档沿用/估计并告知，setting_basis不进入角色描述正文。

选材计划是唯一语义来源：例如袜料写`{"id":"stocking_surface","kind":"material","stages":["front","back","left"],"target":"实际可辨的透明度、织物和图案尺度","crop":[10,20,300,400]}`（坐标须换成本图实际值）。对应prepare自动加入原生局部、提示词和planned_复核项；同类别最多三块合成一个比例适中的辅助输入，保留来源、裁切、原生与显示尺寸、真实缩放及分区旁录；位置说明自动进入提示词。没有新修复时不再复制这段目标到后续critical_constraints，也不另写选材表。阶段references只写必要上游和计划以外的证据；已列入uses的原图/局部自动处理，不手动重复占槽。接口返回与记录保留reference_coverage，可核对各特征实际进入哪个输入。planned_为保留前缀。拼合后目标仍绑定各自原始证据；实际输入编号按reference_index读取，reference_region为拼图内分区，reference_key保留原始目标证据键，不能把整张拼图当作所有检查的共同答案。

背面prepare自动同时加入正面原图（front_material，仅材料纹理与表面光影）和镜像剪影（back_silhouette，仅身形比例与轮廓趋势，不要求完全一致）。后方设计以back/rear_oblique原图为准；未引用后方原图时自动补一张back优先依据。普通stage:front保留为材质原图，另加剪影；直接传正面素材或同文件别名也强制限定材质角色，不能用role误称后方设计。手写panels不能把正面材质与后方设计混成一个未区分用途的参考；自动容量拼图可以同图分区，并逐区保留不同guide和目标来源。front_material可由source_id/file/panels提供，但不能把它标为rear_design以绕过用途限制。正面缺少可靠Alpha时报告无法可靠提取剪影，按既有背景处理规范准备并实际核验分割；不能直接按白色RGB删除背景，也不现场研究源码。后方参考中的裙层、后摆、背片及装饰优先于正面生成图；前后细节无需相同。这个规则同时进入固定提示词、对照和复核目标，不另外抄写；rear_reference是接口保留的检查项id，角色约束另取id。

后续阶段只写`prompt`及确需的本阶段配置，不重复角色/素材分析。参考选择一种：`source_id`为已采用原图；`stage`为有效上游；`file`为已有辅助图；`panels`可自动整理同主题原生裁切，一项参考占一个工具槽位。每项必须写限定用途`role`，辅助图可写固定`guide`。例如正面输入`{"stage":"head","role":"身份基准"}`。图像工具默认最多5项参考，能力确有不同才填max_reference_images。prepare先应用已记录的清晰替代关系、合并同文件用途，再在超限时自动拼合，直到满足数量；不丢掉任何保留的必要原图或局部。优先拼正面材质原图+剪影，再考虑同用途证据；若仍超限继续合并。搜索横排/竖排/多行布局，优先紧凑、少空白；上下图等宽，同排图等高，保持各图比例（允许整数像素舍入）。整图9/16≤宽/高≤4/3；只有超限才补最少空白，不强制正方形。较小图等比例放大对齐，不旋转、不缩小关键纹理，原始素材保留。唯一编辑底图始终独立；容量连底图及一张必要参考都装不下时明确报错。接口返回reference_packing与reference_coverage，不需要另外status或看源码。

所有通用构图、光照、表情、背景、质量、编辑保留语句均自动插入。模型不得写自由通用段落。角色变量详细字段只在需要时查[提示词字段](prompt-construction.md)，不读生成器源码。`critical_constraints`同时生成验收设计目标，identity同时生成身份目标，用户覆盖项同时生成呈现目标；不再另写同义验收清单。

可选顶层字段：`reviewer_policy`、`generation_limit`（默认6）、`required_calls`（默认3，例外须`user_override_reason`）、`strategy_change`、`review_regions`、`comparisons`、`max_reference_images`、实际`tool/parameters`。reviewer_policy为`{"mode":"prefer_subagent","model":null}`；用户指定模型填实际名称；禁用填`{"mode":"self","model":null,"user_override_reason":"用户原话"}`。

来源局部不另写裁图脚本，直接在同一prepare的references中使用：

```json
{"role":"本轮服装材料和肩部位置依据","panels":[{"source_id":"s001","crop":[0,300,500,700]},{"source_id":"s002","crop":[100,200,550,650]}]}
```

panels是一至三个已采用原图的同主题局部，坐标来自实际图片。脚本等比例对齐排版并保存来源/裁切、native_size/display_size及actual_scale_xy旁录；不自动缩小关键细节；每个参考项仍要保留身份与连接上下文。

只有本轮已出现明确歧义、比较尺度需要或小细节在默认拼图看不清时提供裁切：

```json
{
  "review_regions":[{"id":"shoulder","crop":[0,500,700,1000]}],
  "comparisons":[{"reference_indices":[1],"candidate_crop":[0,500,700,1000],"check_ids":["design_shoulder"]}]
}
```

坐标必须来自实际图片；示例不是通用坐标。默认按身份/设计检查的实际来源分组并去重，不把全部素材两两拼完。服装比较肩、颈根、上臂位置和露肤范围；合理小偏移接受，明显改穿法不能按同款同色通过。

## 当前阶段才继续阅读

生成工具返回后，按接口返回动作读[生成结果、复核和排序接口](production-review-api.md)的相应段落。四阶段选定后才读[交付接口](production-delivery-api.md)。制作记录由脚本生成，普通制作不研究记录Schema、打包或网页实现。

## 制作记录格式


解析入口固定`制作记录/制作记录.json`，规范见[记录契约](production-record-format.md)和[JSON Schema](production-record.schema.json)。该JSON及三份Markdown均由脚本生成；模型不要重新撰写、移动字段或补造历史。
