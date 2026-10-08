"""Fixed common generation text. Character facts belong in build_prompt's spec."""

TEMPLATE_ID = 'character-reference-common-2026-10-09-reference-capacity'
STAGES = {'head': '头部特写', 'front': '全身正面', 'back': '全身背面', 'left': '人物自身左侧全身'}
RATIOS = {'head': '3:4', 'front': '9:16', 'back': '9:16', 'left': '9:16'}
FRAMING = {
    'head': {
        'required': '3:4头部近景，人物朝前，相机与双眼同高，头部自然端正、目光看向镜头，面部大致居中。'
                    '略微拉远取景以容纳完整头部轮廓；头顶主体及重要头饰完整入画，最高重要轮廓上方留出明显空白。'
                    '胸部及以上取景，服装只呈现按原图穿戴位置自然落入画内的部分，不为展示衣服抬高领口。',
        'preferred': '脸为画面主体；协调整体镜头距离和位置，保留顶部呼吸空间，仍保持头部近景，不扩展为半身像。'
                     '长马尾及长发束可以自然延续出左右画边，不要求全部长发入画；零散飞丝触边允许。',
    },
    'front': {
        'required': '9:16正面全身，近正交、平视，从完整头顶主体及重要头饰到鞋底全部入画；'
                    '自然直立站姿，双脚稳定，双臂放松并与躯干稍分开，双手自然可见；头部端正、双眼注视镜头。',
        'preferred': '人物完整且占比较大，头顶和鞋底留适度呼吸空间；保持原有头身与身材比例，不拉长腿、不缩小头部。',
    },
    'back': {
        'required': '9:16背面全身，近正交、平视，身体和头部背向镜头，不明显回头；'
                    '从完整头顶主体及重要头饰到鞋底全部入画，自然直立站姿，双臂放松并与躯干稍分开。',
        'preferred': '人物完整且占比较大，头顶和鞋底留适度呼吸空间；沿用正面的头身与身材比例，后方设计按有效依据及已采用方案连接。',
    },
    'left': {
        'required': '9:16人物自身左侧全身，近正交、平视，鼻尖朝画面左，头部与身体同向、整体接近侧面；'
                    '从完整头顶主体及重要头饰到鞋底全部入画，自然直立站姿。',
        'preferred': '人物完整且占比较大，头顶和鞋底留适度呼吸空间；沿用正面、背面的头身与身材比例和共同结构，允许自然小幅转角。',
    },
}
CHANGES = {
    'head': '将参考转换为独立头部近景；采用自然端正头态与正视镜头；略微拉远取景以容纳完整头部轮廓',
    'front': '将参考转换为独立正面全身图；采用自然站姿与正视镜头',
    'back': '将同一角色和本套造型转换为独立背面全身图；按有效依据及已采用方案呈现后方结构',
    'left': '将同一角色和本套造型转换为独立左侧全身图；按有效正面、背面及原始依据连接共同结构',
}
LIGHTING = '普通摄影棚柔光，均匀、自然、接近中性；忽略参考场景光，背景色和背景光不影响角色，角色独立光照。'
EXPRESSION = '自然中性表情，嘴唇自然轻合，不微笑。'
BACKGROUNDS = {'transparent': '透明背景，无场景。', 'white': '白色背景，无场景。'}
QUALITY = ('保持角色本色、材料与原画风，画面清楚、干净。头发按实际像素占比呈现可辨的发束边界与层次，'
           '不继承原图明显模糊，不糊成一片；面部保留自然妆容、明暗和真实标记，纠正无依据脏污与涂抹。'
           '边缘自然、干净，无文字说明、版框、水印或额外物体。')
BACK_REFERENCE_RULE = ('有背面或侧后原图时，后方发饰、背片、裙层、后摆和装饰以该角度原图的实际可见设计为准。'
                       '同时使用正面原图和剪影：剪影只参考身形比例与轮廓趋势，不要求投影完全一致；'
                       '正面原图只参考材料、纹理及物体表面光影，不参考具体服装设计、裁片、裙层、系带或装饰位置。'
                       '前后同套服装不等于前后细节相同，不复制正面的胸饰、系带或裙层到背面。'
                       '原图未显示之处采用与可见设计相容的合理连接；头发或垂带的自然遮挡可以保留，'
                       '不为展示隐藏接缝挪开头发，不要求不可见的后片细节得到证明。')
EDIT_CLEAN = '保持画面色彩、细节干净，不要添加噪点、颗粒、污渍、褶皱。'
GUIDE_ROLES = {
    'back_silhouette': '只参考身形比例与整体轮廓趋势，不要求完全一致，不提供背面服装设计或材料答案。',
    'front_material': '正面原图仅参考材料质感、纹理尺度及物体表面光影；排除场景色光，服从统一柔光。'
                      '不得参考具体服装设计、裁片、裙层、胸前系带或装饰位置，不把正面布局串到背面。',
    'rear_design': '背面原图：后方发饰、背片、裙层、后摆及装饰以图中实际可见内容为准。',
}
GUIDES = {
    'front_material': '图{index}是正面材料参考。' + GUIDE_ROLES['front_material'],
    'rear_design': '图{index}是背面或侧后原始参考，优先决定后方可见设计；只沿用该图实际能辨认的内容，'
                   '不因正面生成图更清楚而替换它的裙层、后摆、饰件布局或背片。',
    'back_silhouette': '图{index}是正确正面沿竖直中轴左右镜像的剪影，人物头上脚下，仅参考身形比例和轮廓趋势，不要求完全一致。'
                       '重新判断后脑、背片、前饰的远侧位置及连接、近远与遮挡，不复制正面内部布局；'
                       '还原为同角色正确的背面材料，不输出黑色剪影或辅助标记。',
    'structure_silhouette': '图{index}是目标视角的实体占据与结构走向辅助，只参考部件的归属、数量、连接和连续路线；'
                            '不将辅助图的灰度、标记或简化表面当作角色配色和材料。',
    'rear_layers': '图{index}仅表示目标视角的前后遮挡与层次，较亮部分更近；不把灰度层级复制成角色颜色，真实材料和本色沿用有效角色依据。',
    'edit_annotation': '图{index}是唯一编辑底图的标注副本，仅指示本次操作位置、方向和范围；原图才是编辑底图，'
                        '线、箭头、编号和标注色不进入输出，不改变未涉及的身份、设计和材料。',
}
CORRECTIONS = {
    'gaze_up': '相机与人物双眼同高，人物头部自然端正。双眼注视镜头，眼球不向上转。'
               '纠正参考中的上视状态，让虹膜和瞳孔回到自然平视的位置；保留原有眼型和眼睛大小，不机械消除所有眼白。',
}


def common(stage, overrides, background_mode):
    """User requests replace one default; generation aims are not review thresholds."""
    fields = dict(aspect_ratio=RATIOS[stage], framing_required=FRAMING[stage]['required'],
                  framing_preferred=FRAMING[stage]['preferred'], lighting=LIGHTING,
                  expression=EXPRESSION, background=BACKGROUNDS[background_mode])
    if not isinstance(overrides, dict) or set(overrides) - set(fields):
        raise ValueError('user_overrides contains unknown fields')
    for key, entry in overrides.items():
        if (not isinstance(entry, dict) or set(entry) != {'value', 'user_quote'}
                or any(not isinstance(entry[k], str) or not entry[k].strip() for k in ('value', 'user_quote'))):
            raise ValueError(f'user_overrides.{key} requires nonempty value and user_quote')
        fields[key] = entry['value'].strip()
    # A changed user ratio must replace the default label inside the common framing too.
    if 'aspect_ratio' in overrides and 'framing_required' not in overrides:
        fields['framing_required'] = fields['framing_required'].replace(RATIOS[stage], fields['aspect_ratio'], 1)
    return fields
