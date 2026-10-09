# 阶段复核与流程助手

本页为特殊评审、后处理和维护的低层接口。普通新制作使用[制作接口](production-api.md)，不重复手工构造机械字段，也不在任务开始读取全部实现。

常规制作把三个问题分开：**图片是否合格、三张偏好哪张、记录能否证明当前版本对应这个结论**。前两项由实际看图的模型判断，哈希、调用计数和依赖由脚本整理。单纯评审不调用生图；仅生成试跑保持未验收。

## 选择复核模式

每阶段生成后，制作模型只看参考—候选拼图，一两句简查取景、身份造型、头发面部和服装位置的明显错误，不展开原生、缩小和背景全流程。尺寸、格式和Alpha检查不算看图。提交自评pass且接口开放 `reviewer_dispatch_ready:true` 后，才可启动、预先启动或再次联系同模型独立上下文子代理；不能先启动代理再补自评记录。

每阶段使用新的复核上下文，返修沿用本阶段原代理。按 `reviewer_handoff.task` 的固定任务交接，只附任务包及报告模板，不传制作模型自评报告、通过结论或推荐答案。子代理指出问题后自己修改、针对问题简单自评，再交回同一代理；无法复用时记录真实原因。用户禁止/指定模型或子代理不支持/启动失败时如实遵从。脚本不启动模型，也不能在平台层面拦截绕过技能的原始代理工具调用，不能仅填agent_id冒充复核。

复核仍查看整体和细节。第二次及后续重点查看上次指出的饰品位置、长度等，按“可以这样设计”判断：只要穿戴、连接和整体关系合理，直接通过；仅达到明显不合理、完全不能接受的程度才拒绝。不要因相对原图仍有合理差异而反复退回，也不新增越来越细的要求。头发、对齐和特写服装的位置尺度见 [验收规则](quality-and-revision.md#原生清晰度验收门槛)。

同模型复核主要降低自评倾向和上下文干扰，不能保证发现主模型看不出的细节。能力不足时诚实保留待核实项，不能用同模型身份或脚本成功替代证据。

## 给复核者的最小证据包

提供用户当前要求、阶段目标及来源、有效原图、当前候选、必要有效上游和空间方案。保留原始文件，不附制作模型的通过结论或推荐答案。返修包另含上次具体问题、原代理id和本次放宽尺度；这是复查范围，不是预设答案。复核者只看图并返回简短观察/结论，不负责生图、三选一排序、哈希、读像素、记录修复或打包。助手生成的 `acceptance_standard/review_context` 随包交给复核者；返回pass时可以明确记录仍有的合理设计差异。

助手在登记首张或选中新增候选时一次生成全部必要证据与pending模板，制作端只看对比，复核者直接复用。先看并排图，再按质量问题选择按部位定位的原生局部和缩小图；已在对照中看清的面板无需再开来源。已看列表只写真正打开的证据id，不把拼图来源自动填为已打开。裁切、实际倍率、来源/输出哈希和排版位置保存在旁录。背景只看一张alpha_compare；脚本从实际透明/近白小区挑最多两个采样候选并标号，模型确认空白填true，其余留false，脚本只使用已确认点读取RGB/Alpha，不要求修删未确认点。

`prepare` 固定目标、来源、配方和前置条件，此时尚未有候选，不渲染候选。登记首张成功输出后，或排序选中新增候选后，助手才生成该候选的证据包和待核实模板；登记处理结果也生成对应包。落选追加图不生成完整复核包。证据包不代表任何图已被查看。`viewed_evidence_ids` 必须由复核者根据实际打开的证据明确填写；裁切/排版完成、工具返回路径或哈希匹配都不等于查看。复核返回按检查项记录来源事实、候选表现、证据位置、比较依据和 `pass/fail/pending`。

## 逐组判断与一次补证

1. 先看参考—候选并排图，候选整幅在其中已清楚可见时不再单开，查身份、明显改款、错误视角/构图、缺件或错误归属等关键否决项。头顶主体及重要头饰完整即可，飞丝触顶允许；特写胸部及以上，不卡锁骨；左侧朝画面左且接近侧面即可，明显约45度斜正面才拒绝。固定目标不能自行加严，用户明确要求除外。明确失败即可停止剩余完整检查，已查失败写具体差异，未查项保持 `pending`；不为拒绝一张明显错误图补齐全部对照。
2. 未明确失败再依次查身份/设计及呈现、原生质量、缩小表面、背景和适用空间关系。每组只呈现两三张必要全图/局部，先看当前组再记观察，不把全部证据塞进大拼图。头发按占用像素看可辨发束与模糊；面部单查无依据灰脏和涂抹，分别写quality_observations。源图缺陷不是照搬目标。标准见 [验收与返工](quality-and-revision.md)，尺度方法见 [实际对照](visual-verification.md)。
3. 看不清、证据不适用或判断冲突时，仅补一次更适合的原生局部/对照，记录补证和新的观察。仍无法确认保留 `pending`，不自动重生成。因修改目标、图片或上游而开展的新复核必须重新绑定版本，不能拿补证次数包装无限重复判断。
4. 按本轮尺度判断通过/失败/待核实。第二次起合理设计差异可明确记录后pass；细节仍看，不把原图位置/长度精确一致作为门槛。简短具体即可，不只写“不像”“质量符合”。达到阶段生成上限则取消合格评价，自己选优继续。

未达到上限时，首张完整复核通过才触发两次原样追加。追加候选只作排名，偏好新增图先简单自评再复核；失败或待核实回退有效首张，不补抽。下游引用有效所选版本；达到上限时引用制作模型已记录选优的未验收版本。完整规则见 [合格视图三选一](quality-and-revision.md#合格视图三选一)。

## 流程助手与配置

有Python/Pillow时必须用 `scripts/review_workflow.py`；Python命令不可用先找环境运行时，确实没有时才依同一顺序人工记录并说明限制。正常制作顺序为固定模板组装→prepare→真实生图→register→制作模型实际看图自评→record-review→status→复核代理。四个命令共同要求 `--root` 和 `--stage head|front|back|left`；前三个另需 `--config` JSON文件，`status` 只读。达到上限或固定追加时按实际next_action处理，不强制给落选追加图自评。脚本不调用生图或复核模型。

```bash
python "<技能目录>/scripts/review_workflow.py" prepare --root "<项目根目录>" --stage head --config "<准备配置.json>"
python "<技能目录>/scripts/review_workflow.py" register --root "<项目根目录>" --stage head --config "<实际调用.json>"
python "<技能目录>/scripts/review_workflow.py" record-review --dry-run --root "<项目根目录>" --stage head --config "<复核结果.json>"
python "<技能目录>/scripts/review_workflow.py" record-review --root "<项目根目录>" --stage head --config "<复核结果.json>"
python "<技能目录>/scripts/review_workflow.py" status --root "<项目根目录>" --stage head
```

v2固定保存 `制作记录/验收目标.json`、`制作记录/验收状态.json`、`制作记录/三选一记录.json` 及 `制作记录/流程状态.json`。前三者均为 `schema_version:2`、`rules_version:2`；流程状态为内部事件/历史账本。移动整个项目根目录即可，不单独更名规范记录。外部输入接受真实绝对路径，助手复制内容快照后在记录中存相对根目录路径及哈希，避免依赖原素材未来是否仍在原位置。仅接受当前格式，不提供旧记录转换或历史检查入口。

### prepare：固定本阶段目标

| 字段 | 内容 |
|---|---|
| `sources` | 来源id到 `{"file":"真实图片或固定文档"}` 的对象；助手快照并哈希 |
| `checks` | 视图到目标数组；每项含 `id/group/target/source_ids`，`look`另有`aspect`。完整分组及可见性规则见 [阶段目标](stage-review.md#固定验收底稿) |
| `recipe` | `prompt_file`、有序 `inputs:[{file,purpose}]`、`tool`、`parameters`；首次production必需 |
| `reviewer_policy` | 默认 `{"mode":"prefer_subagent","model":null}` 表示同模型继承；用户指定模型写确切模型名。用户禁用时用 `{"mode":"self","user_override_reason":"用户实际要求"}` |
| `required_calls` / `user_override_reason` | production默认3；用户明确限制时可为1或2并保存实际要求。首张批准后不能临时减少额度 |
| `task_mode` / `scope` | 默认 `production/full_sheet`；既有图片用 `existing_review`，单张用 `single_stage`，无需虚构生成配方/历史 |
| `strategy_change` | 达到同一问题三次失败后，可提交 `issue_key/diagnosis/change` 记录一次具体策略变化；不重置失败次数 |
| `generation_limit` | 默认6，用户可明确另定正整数。追加、编辑和工具失败都计真实调用，跨配方和重新prepare累计；同一问题三次失败再换一次策略仍失败可提前停止 |

`look/aspect:"framing"`由prepare规范化为统一默认尺度，制作模型不能靠固定目标加严。用户明确要求覆盖时，该项另写`requirement_origin:"user"`和`user_requirement:"用户实际原话"`，保存真实依据，不虚构要求。

配置片段如下，`checks` 必须按真实任务补齐全部适用目标，不能直接把这个单项当完整表：

```json
{"sources":{"primary":{"file":"参考/原图.png"}},
 "checks":{"head":[{"id":"头饰连接","group":"design","target":"按已固定原图事实描述连接对象及位置","source_ids":["primary"]}]},
 "recipe":{"prompt_file":"制作记录/本次提示词.txt","inputs":[{"file":"参考/原图.png","purpose":"身份及画内造型"}],"tool":"实际生图工具","parameters":{"transparent_background":true}},
 "reviewer_policy":{"mode":"prefer_subagent","model":null}}
```

修改配方、目标、复核策略或上游时，`prepare` 归档旧阶段记录，保留调用累计和原复核代理的问题上下文；旧批准不带到新条件。简单自评失败或复核确证失败可在预算内修复；待核实时只补证。达到上限改为制作模型选优继续。

### register：登记真实调用或处理

| 种类 | 配置字段与约束 |
|---|---|
| production成功调用 | `id`、`output`、`evidence_file`、`recipe`。`recipe`结构同prepare，内容必须完全一致；证据须为非空真实请求/结果记录，输出须是完整PNG |
| production工具失败 | `id`、`error`、`evidence_file`、`recipe`，不写`output`。首张批准后的失败占用一个固定追加额度，不能补抽 |
| existing_review导入 | `id`、`output`；不写虚构`recipe/evidence_file`。助手生成的是带来源/哈希的导入回执，明确标为导入，不冒充历史生成日志 |
| 后处理 | `kind:"processing"`、`id`、`output`、`record_file`。先完成当前选图；真实JSON旁录含当前`input_sha256`或`source_sha256`及实际`output_sha256`。普通处理重新复核，已批准图的可证明无损处理走下述processing-check |
| 可选对照区域 | 成功调用可带`review_regions:[{"id":"face","crop":[x0,y0,x1,y1]}]`生成原生局部；`comparisons:[{"reference_ids":["source_primary"],"reference_crops":{"source_primary":[x0,y0,x1,y1]},"candidate_crop":[x0,y0,x1,y1]}]`定制并排图。坐标来自实际图片，不机械照抄；无需裁切时用自动全图对照 |

id为1—80位字母、数字、下划线或横线，跨当前阶段历史不能重复。成功调用示例：

```json
{"id":"head-extra1","output":"实际工具输出.png","evidence_file":"制作记录/真实请求结果.json",
 "recipe":{"prompt_file":"制作记录/本次提示词.txt","inputs":[{"file":"参考/原图.png","purpose":"身份及画内造型"}],"tool":"实际生图工具","parameters":{"transparent_background":true}}}
```

输出归档到按真实顺序编号的 `候选/`。首张成功输出先生成 `制作记录/复核任务/<stage>-<id>.json` 证据包和 `-self-check-template.json` 制作自评模板；此时不生成代理报告模板，`reviewer_dispatch_ready:false`。自评通过后才生成 `-report-template.json`，status验证当前候选/目标/依赖/证据并返回 `reviewer_handoff`。只把handoff交给复核者，不附自评内容。固定追加两张只登记，排序偏好新增图时才给它走同样的自评门禁。后处理走实际处理分支，无损证明另见下文。模板默认pending、空观察和空已看列表，不自动填通过或已查看；来源PNG按哈希复用。

既有单张评审先 `prepare` 提交 `task_mode:"existing_review"`、`scope:"single_stage"`、实际`sources/checks`，再 `register` 导入图片；完成视觉复核后 `status` 为 `review_complete`，只能用 `--stage` 门禁，不授权完整套或下游。既有整套评审可用`full_sheet`按真实依赖导入，不要求生成历史。

### record-review：提交看图事实或排名

简单自评先提交下面的轻量记录；`observation` 写整体及相关细节的实际观察，返修时指出上次问题是否改善，失败另写稳定 `issue_key`。这是制作模型自评，无需“子代理不可用”原因：

```json
{"kind":"self-check","call_id":"head-first","packet_sha256":"实际任务包哈希",
 "reviewer":{"mode":"self","model":"inherited"},"result":"pass",
 "viewed_evidence_ids":["compare_001"],
 "observation":"实际看拼图后填写明显问题和上次问题改善情况的一两句观察"}
```

已看id按实际内容填写，不照抄示例或自动列全包。简单自评只要求实际看参考—候选拼图，不要求whole、native、reduced或背景；完整质量复核仍由独立复核者执行。观察必须来自实际看图，脚本只能检查记录完整性。只有自评pass并获交接就绪才交代理，自评fail自己改；新增优胜候选fail直接回退有效首张，不多抽图。落选追加图无需自评/复核。

| 字段 | visual报告 |
|---|---|
| `kind/call_id/packet_sha256` | `kind:"visual"`，实际候选或当前处理id，以及实际任务包文件的SHA-256 |
| `reviewer` | 子代理：`{"mode":"subagent","model":"inherited或实际模型","agent_id":"实际代理id"}`；指定模型须一致。自评：`mode:"self"`、实际`model`、`reason_code/reason`；原因码为用户禁用的`user_disabled`或默认子代理不可用的`unsupported/startup_failed` |
| `viewed_evidence_ids` | 实际打开的包内证据id，无重复；不能自动把全部证据列为已看 |
| `checks` | 每项含`id/result/reference_observation/candidate_observation/evidence_ids`；身份、设计、空间另需`comparison_basis`；可用稳定`issue_key`累计同一关键失败，不能换标签清零。结果仅`pass/fail/pending`，证据id必须属于明确已看列表 |
| `quality_observations` | 完整通过报告分别填写`hair`和`face`实际观察，或明确本视图不可见的原因；不能用文件/Alpha检查替代 |
| `background_policy` | `transparent`及真实`alpha_source`，或`exact_white`，或带实际限制依据的`near_white_rgb_fallback`，详见 [背景记录](stage-review.md#看图后填写当前状态) |
| 背景项的 `empty_background_samples` | 看图者确认真实空白后提交 `[{"xy":[x,y],"confirmed_empty":true}]`；助手读当前PNG实际RGB/Alpha，不要求模型手抄像素 |
| `supplement_round/extra_evidence` | 首次0（默认），唯一一次补证1；补充证据含唯一`id/file/kind`，必要时`background:"light"或"dark"`。kind为适用`whole/native/reduced/background/source/upstream`。保留首次报告，不覆盖历史 |
| `reviewer_replacement_reason` | 返修无法继续使用包中原agent_id时填写真实原因；不能无理由换代理重启严格首审 |

包内典型证据id为`whole`、`compare_001`等并排图、`native_001`等原生分块、`region_face`等指定区域、`reduced`、`alpha_compare`、`source_<来源id>`、`upstream_<视图>`；按包内实际内容引用。原生质量须至少一个native，缩小表面须至少一个reduced，背景须background；可附source/upstream等上下文，脚本标为context_reference，不拒绝有效的比较依据。身份/设计/空间需适用原始或上游对照及候选，不能只看生成图互证。

先用`record-review --dry-run`预检，它一次返回可收集的`field_errors`和字段位置，不保存报告、不改变状态、不批准图片。修正字段后用相同配置去掉`--dry-run`正式登记；记录修复不触发生图，也不改变原先视觉失败。背景策略模板从真实输出推导：有透明度且真实调用要求Alpha时使用model；导入或其他来源不明时留空待确认，不捏造Alpha来源。

首项明确失败可只提交已查项，未查项仍待核实。只有全部目标齐全且通过才批准；存在fail则拒绝，其余为未验收。已批准/拒绝不反复提交同图；首次待核实时可提交轮次1并补齐必要证据；补证报告重新提交完整的当前观察集合，助手不自动继承上一份的pass项，通过仍需覆盖全部目标。仍pending保留限制或回退有效首张。

排名使用独立报告，不需要逐项检查表：

```json
{"kind":"selection","preferred_call":"head-first","selection_reason":"实际比较后填写具体偏好依据",
 "viewed_call_ids":["head-first","head-extra1","head-extra2"],
 "reviewer":{"mode":"self","model":"inherited"}}
```

`viewed_call_ids`须恰好包含本组实际成功且已看的候选；失败调用没有图片，不写入已看列表。排序固定由制作模型自己执行，不遵循独立视觉复核的子代理策略。接近时保留合格首张，仅明显改善才选新增图。排名一经记录不能改排另一张补救；偏好新增图后只复核它，拒绝或一次补证后仍pending则自动记录采用有效首张，追加次数不增加。

复核交接通用任务直接使用接口返回的 `reviewer_handoff.task`，不另拟审核提示词；正常推进不为交接另跑status。任务包自动带generation_context：本次实际提示词全文、真实有序输入用途与分区。提示词只解释参考分工，不能当作通过答案或更严格门槛。任务包、模板、根目录和必要补证是实际变量；返修信息由review_context提供，不附制作自评结论。既有图或后处理没有新的登记配方时如实not_recorded，不猜测历史提示词。

### 无损后处理：只简查构图和背景

旁录`operation:"png_reencode"`表示同尺寸重编码；`operation:"transparent_canvas_pad"`表示只增加透明画布，并填写整数`offset:[x,y]`（未填为[0,0]）。输入原画布必须完整保留；脚本重算全部Alpha及所有Alpha>0处RGB，允许透明像素隐藏RGB变化。任何可见颜色/Alpha变化、裁切或形变均不能冒称无损；其他操作走普通复核。

原批准、目标和上游仍有效且像素证明通过时，任务包只需当前全图及深浅底。制作模型实际检查后提交：

```json
{"kind":"processing-check","call_id":"真实处理id","packet_sha256":"实际任务包哈希",
 "reviewer":{"mode":"self","model":"inherited"},"result":"pass",
 "framing_observation":"实际构图观察","background_observation":"实际深浅底观察",
 "viewed_evidence_ids":["whole","alpha_compare"]}
```

通过后以`approval_basis:"pixel_equivalent_with_framing_check"`继承原质量批准，保留旧完整复核快照及真实像素证明；不伪造新一轮逐项通过。当前文件绑定更新，已批准人物内容的`dependency_binding_sha256`不变，未改变的下游不重审。简查fail/pending不恢复批准；目标/上游变更仍失效。未验收上限选图不能通过无损处理自动变成合格。

### status：按下一步动作执行

| `next_action` | 行动 |
|---|---|
| `prepare` / `generate_first` | 前者先固定目标；后者才由助手实际调用首张生成并登记 |
| `review_first/review_selected/review_processed` | 打开返回的任务包，执行实际复核，不生成新图 |
| `self_check_first/self_check_selected` | 制作模型先简单看整体及细节，提交self-check；通过后才交子代理，返修交回review_context中的原代理 |
| `self_check_processed` | 脚本已有无损证明，制作模型看当前全图和深浅底后提交processing-check，不重做完整复核 |
| `generate_extra` | 仅在首张批准有效时，原样并行调用剩余两项；全部返回后由制作接口一次receive，中间不看图或单张登记 |
| `compare_candidates` | 制作模型自己比较返回的实际成功候选并提交一次selection，接近时保留首张 |
| `supplement_evidence/blocked_uncertain` | 前者只补一次更合适证据；后者保持待核实并报告，不自动生成 |
| `repair_records/repair_records_or_reprepare` | 修缺失记录、路径或绑定；目标/图片确实改变才重建并复核，不能靠改字段恢复通过或触发生图 |
| `revise_input_or_repair/stop_and_diagnose` | 明确失败才修复；同一问题三次失败后诊断并改变一次策略 |
| `select_best_at_limit` | 本阶段取消合格评价，制作模型自己比较返回的全部可用候选并提交limit-selection，不交子代理、不生成新图 |
| `report_tool_failure/report_processing_failure` | 如实报告实际失败，保留已有合格部分；不补抽追加，不沿用失败处理图 |
| `continue/review_complete` | 前者在返回门禁有效时可进入下游；后者结束本次单张评审。均只表示记录中的批准有效 |

## 门禁和报告

达到上限时提交下面的记录，`viewed_call_ids` 列本阶段（含归档历史）实际查看的全部成功候选。工具失败没有图，不列为已看；无法查看的损坏/缺失数据先修文件记录，不重生成：

```json
{"kind":"limit-selection","preferred_call":"实际最优候选id",
 "selection_reason":"实际比较后的选择依据","viewed_call_ids":["所有实际成功候选id"],
 "reviewer":{"mode":"self","model":"inherited"}}
```

助手保存真实上限原因、累计调用绑定及选图报告，直接允许下游引用该图。选中仍有效的合格图时复用原批准，不重新复核；否则采用状态记为`selected_unreviewed`，阶段和完整交付门禁的`record_integrity_valid/downstream_ready`可以为true，`recorded_approval_valid`仍为false，视觉字段为`selected_without_approval`。不补造检查表、已查看或pass。上限后的本地后处理保留真实版本链，未验收图仍未验收，不重新开启合格评价。既有图片单纯评审不使用此生成上限例外。

首张复核后先用门禁的 `--baseline` 检查首张批准，才开始两次追加；完成选择后用阶段门禁，进入下游前检查有效依赖。阶段目标、图片、证据和上游变化只失效受影响的批准，后续阶段新增记录不应撤销已完成阶段。完整交付另检查四视图关系和总览排版。

分别报告文件完整性、记录/版本一致性、记录中的视觉判断。文件或记录检查通过不能表述为图片合格；标明视觉结论来自子代理还是自评，指出未完成、待核实和降级原因。修复机械字段后只能重新核查记录，不能自动把原先视觉失败改为通过。
