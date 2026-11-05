"""UI strings (zh/en) and the optional preference file."""

import json
import os
from pathlib import Path

LANGUAGES = {"zh": "中文", "en": "English"}

TEXT = {
    "tab_overview": ("概览", "Overview"),
    "tab_tasks": ("任务", "Tasks"),
    "tab_behaviors": ("行为", "Behaviors"),
    "tab_chassis": ("底盘", "Chassis"),
    "tab_extensions": ("扩展", "Extensions"),
    "tab_joints": ("关节", "Joints"),
    "tab_display": ("显示", "Display"),
    "tab_plots": ("曲线", "Plots"),
    "tab_safety": ("安全", "Safety"),
    "plot_title": ("曲线 {n}", "plot {n}"),
    "module_plot": ("曲线", "plot"),
    "plot_add": ("新增曲线", "add plot"),
    "plot_remove": ("删除曲线", "remove plot"),
    "plot_series": ("序列 {n}", "series {n}"),
    "plot_none": ("—", "—"),
    "stop_port": ("停止 {name}", "stop {name}"),
    "safety_header": ("统计|值|每秒", "stat|value|per second"),
    "overview_time": ("耗时 (ms)：QP 求解 / 整周期", "Time (ms): QP solve / full cycle"),
    "latency_header": ("统计|值", "stat|value"),
    "safety_fresh": ("反馈：正常", "feedback: fresh"),
    "safety_stale": ("反馈：**过期**", "feedback: **stale**"),
    # Labels for the server's stats; a stat without one shows its raw name.
    "stat_cycles": ("周期数", "cycles"),
    "stat_overruns": ("超时周期", "overruns"),
    "stat_repeated_reads": ("重复读取", "repeated reads"),
    "stat_guard_rejects": ("守卫拒绝", "guard rejects"),
    "stat_guard_clamps": ("守卫截断", "guard clamps"),
    "stat_held_cycles": ("保持周期", "held cycles"),
    "stat_stale_cycles": ("反馈过期周期", "stale cycles"),
    "stat_resyncs": ("重新同步", "resyncs"),
    "stat_failed_cycles": ("失败周期", "failed cycles"),
    "stat_last_cycle": ("本周期耗时", "last cycle"),
    "stat_cycle_p50": ("耗时 p50", "cycle p50"),
    "stat_cycle_p99": ("耗时 p99", "cycle p99"),
    "stat_cycle_p999": ("耗时 p99.9", "cycle p99.9"),
    "stat_max_cycle": ("最大耗时", "max cycle"),
    "stat_max_lateness": ("最大延迟", "max lateness"),
    "waiting": ("等待状态", "waiting for state"),
    "control_shared": ("控制权：共享", "control: shared"),
    "control_mine": ("控制权：**本 viewer**", "control: **this viewer**"),
    "control_other": ("控制权：客户端 **{holder}** 持有", "control: held by client **{holder}**"),
    "exclusive": ("独占控制", "exclusive control"),
    "home_all": ("全部回零", "home all"),
    "home_all_confirm": ("让每个部位回零？正在驱动的模块优先，其余部位用未重叠的模块回零。", "Home every part? Driving modules first, then non-overlapping ones."),
    "defaults_all": ("全部恢复默认参数", "restore all defaults"),
    "run": ("运行", "run"),
    "pause": ("暂停", "pause"),
    "step": ("单步", "step"),
    "step_cycles": ("单步周期数", "step cycles"),
    "run_running": ("引擎：运行中", "engine: running"),
    "run_paused": ("引擎：**已暂停**（保持 {held:.0f} 个周期）", "engine: **paused** ({held:.0f} held cycles)"),
    "defaults_all_confirm": ("把所有模块的可调参数恢复为默认值？", "Restore every module's tunable parameters?"),
    "confirm": ("确认", "Confirm"),
    "cancel": ("取消", "Cancel"),
    "residuals": ("残差", "residuals"),
    "plot_time": ("时间 [s]", "time [s]"),
    "model_info": ("频率 **{rate:g} Hz** · nq {nq} · nv {nv}", "rate **{rate:g} Hz** · nq {nq} · nv {nv}"),
    "enabled": ("启用", "enabled"),
    "home": ("回零", "home"),
    "running": ("运行中", "running"),
    "idle": ("空闲", "idle"),
    "disabled": ("已禁用", "disabled"),
    "held_by": ("被 **{by}** 占用", "held by **{by}**"),
    "preempted": ("被抢占 {n}×", "preempted {n}×"),
    "params": ("调参", "Tuning"),
    "solver": ("求解器参数", "Solver parameters"),
    "defaults": ("恢复默认", "restore defaults"),
    "failed": ("**{what}** 失败（{code}）：{error}", "**{what}** failed ({code}): {error}"),
    "bad_input": ("**{what}** 输入无效：{error}", "**{what}** invalid input: {error}"),
    "joint_filter": ("筛选", "filter"),
    "joint_header": ("关节|类型|值|下限|上限", "joint|type|value|lower|upper"),
    "language": ("语言", "language"),
    "dark_mode": ("深色模式", "dark mode"),
    "show_grid": ("网格", "grid"),
    "show_robot": ("机器人模型", "robot meshes"),
    "show_skeleton": ("骨架与坐标系", "skeleton and frames"),
    "show_gizmos": ("位姿控制柄", "pose gizmos"),
    "show_measured": ("实测位姿虚影", "measured pose ghost"),
    "show_planned": ("规划目标点", "planned targets"),
    "show_labels": ("文字标签", "labels"),
    "chassis_vectors": ("底盘速度箭头", "chassis velocity arrows"),
    "chassis_trail": ("底盘轨迹", "chassis trail"),
    "show_gaze": ("注视点", "gaze focus"),
    "show_com": ("质心与支撑多边形", "CoM and support polygon"),
    "follow_camera": ("跟随底盘", "follow chassis"),
    "overlays": ("模块叠加（实测新鲜时按实测，否则按命令）", "module overlays (at the measured state when fresh, else the command)"),
    "reset_camera": ("重置视角", "reset camera"),
    "render_hz": ("刷新率 [Hz]", "render rate [Hz]"),
    "prefs_note": ("偏好保存在 `{path}`", "preferences are saved to `{path}`"),
    "prefs_off": ("偏好不保存（--no-prefs）", "preferences are not saved (--no-prefs)"),
    "not_drawn": ("关节 **{name}**（{type}）未绘制", "joint **{name}** ({type}) is not drawn"),
    "reconnect_failed": ("**重连失败**：{message}。正在重试……", "**reconnect failed**: {message}. Retrying..."),
    "disconnected": ("**已断开** {endpoint}：{reason}。正在重连……",
                     "**disconnected** from {endpoint}: {reason}. Reconnecting..."),
    "bad_frame": ("坐标系 **{frame}** 不是 URDF link 或 joint，按世界系显示",
                  "frame **{frame}** is not a URDF link or joint; shown in the world frame"),
}

# Tune-panel labels: "type.path", then "path", then the last key; (zh, en[, hint_zh, hint_en]).
PARAMS = {
    "weight": ("权重", "Weight"),
    "position_weight": ("位置权重", "Position weight"),
    "orientation_weight": ("姿态权重", "Orientation weight"),
    "gain": ("增益", "Gain"),
    "lm_damping": ("LM 阻尼", "LM damping"),
    "tolerance": ("到达容差", "Tolerance", "rad 或 m，每个关节", "rad or m, per joint"),
    "position_tolerance": ("位置容差", "Position tolerance", "m", "m"),
    "orientation_tolerance": ("姿态容差", "Orientation tolerance", "rad", "rad"),
    "priority": ("优先级", "Priority", "关节占用优先级，高者抢占", "Claim priority; higher preempts"),
    "on_held": ("被占用时", "On held", "wait：恢复后继续原目标；abort：放弃目标", "wait resumes the target, abort drops it"),
    "hold_idle": ("空闲保持", "Hold idle", "空闲时保持最后目标；关闭则让出关节给其他任务",
                  "Idle: hold the last target; off hands the joints to other tasks"),
    "claim_idle": ("关节控制", "Joint control", "空闲时也占用这些关节，冲突任务保持暂停",
                   "Idle: also claim these joints so conflicting tasks stay paused"),
    "release_after_s": ("释放延时", "Release after", "最后一次关节输入后多久 (s) 才让给其他任务申请，0 = 立即",
                        "Other requests wait until this long (s) after the last joint input; 0 = at once"),
    "home_motion": ("回初始", "Home motion"),
    "home_motion.velocity_scale": ("速度比例", "Velocity scale", "回初始速度 = 比例 × 关节速度上限",
                                   "home velocity = scale × joint velocity limit"),
    "home_motion.timeout_s": ("无进展超时 (s)", "No-progress timeout (s)"),
    "joint_target.trajectory": ("速度规划", "Velocity planning"),
    "joint_target.trajectory.trigger": (
        "模式", "Mode",
        "direct 直通：目标直接给 QP；threshold 阈值触发：目标跳变超过阈值才规划；continuous 持续规划：始终规划（流式目标会滞后）",
        "direct: the target goes to the QP as is; threshold: plan only on a jump above the threshold; "
        "continuous: always planned (a streamed target lags)"),
    "joint_target.trajectory.sync": (
        "同步", "Synchronization",
        "time 时间同步：各关节同时到达；phase 相位同步：同时到达且关节空间走直线；none：每个关节各自最快",
        "time: the joints arrive together; phase: together on a straight line; none: each as fast as it can"),
    "joint_target.trajectory.threshold": ("阈值 (s)", "Threshold (s)", "目标偏差 / 速度上限超过该时间才规划",
                                          "plan when |target - command| / vmax exceeds it"),
    "joint_target.trajectory.acceleration_factor": ("加速度倍率", "Acceleration factor",
                                                    "加速度上限 = 倍率 × 关节速度上限",
                                                    "max acceleration = factor × joint velocity limit"),
    "joint_target.trajectory.jerk_factor": ("加加速度倍率", "Jerk factor", "加加速度上限 = 倍率 × 加速度上限",
                                            "max jerk = factor × max acceleration"),
    "joint_target.trajectory.velocity_scale": ("速度比例", "Velocity scale", "规划速度 = 比例 × 关节速度上限",
                                               "planned velocity = scale × joint velocity limit"),
    "joint_target.trajectory.velocity_cap": ("速度上限", "Velocity cap"),
    "frame_task.trajectory": ("运动限制", "Motion limits"),
    "gaze.max_velocity": ("最大角速度", "Max velocity", "rad/s，0 = 不限", "rad/s, 0 = unlimited"),
    "gaze.target_idle_s": ("空闲回初始", "Idle to home", "目标静止多久后头部回到初始姿态 (s)，0 = 不回",
                           "Seconds of still targets before the head returns to its initial pose; 0 = never"),
    "center_of_mass.margin": ("边缘余量", "Margin", "m，投影距多边形边的余量；负值允许超出",
                              "m kept inside the polygon edges; negative allows that much outside"),
    "center_of_mass.gain": ("增益", "Gain", "每周期可用余量比例，(0, 1]", "fraction of the slack usable per cycle, (0, 1]"),
    "center_of_mass.recovery_speed": ("回收速度", "Recovery speed", "m/s，超出余量后拉回的速度",
                                      "m/s, pull-back speed once outside the margin"),
    "center_of_mass.closed_loop": ("闭环", "Closed loop", "反馈新鲜时也约束实测状态", "also bound the measured state while feedback is fresh"),
    "constraint.d_min": ("最小距离", "Min distance", "m，碰撞对之间保持的距离", "m kept between pairs"),
    "constraint.gain": ("接近增益", "Approach gain", "1/s，高于 d_min 每米允许的接近速度",
                        "1/s, approach speed per metre above d_min"),
    "constraint.activation_margin": ("激活余量", "Activation margin", "m，距离小于 d_min + 余量时生效",
                                     "m, rows appear within d_min + margin"),
    "constraint.hysteresis": ("滞回", "Hysteresis", "m，已激活的碰撞对多保持的距离", "m an active pair stays active beyond the margin"),
    "constraint.approach_deceleration": ("接近减速度", "Approach decel", "m/s²，0 = 不制动", "m/s², 0 disables braking"),
    "velocity_timeout": ("速度超时", "Velocity timeout", "s，障碍物位姿停止更新后多久速度归零，0 = 关闭",
                         "s after the last pose write before obstacle velocity resets; 0 disables"),
    "closed_loop": ("闭环", "Closed loop", "反馈新鲜时也约束实测状态", "also bound the measured state while feedback is fresh"),
    "null_space_preference.posture_weight": ("姿态权重", "Posture weight"),
    "null_space_preference.posture_velocity_scale": ("姿态速度比例", "Posture velocity scale", "× 关节速度上限，(0, 1]",
                                                     "× joint velocity limit, (0, 1]"),
    "null_space_preference.manipulability_weight": ("可操作度权重", "Manipulability weight"),
    "null_space_preference.joint_limits_weight": ("限位权重", "Joint limits weight"),
    "null_space_preference.joint_limits_band": ("限位带宽", "Joint limits band", "rad 或 m，距限位多近开始推离",
                                                "rad or m from a limit where the push starts"),
    "null_space_preference.joint_limits_rate": ("推离速度", "Push rate", "rad/s 或 m/s，完全激活时的推离速度",
                                                "rad/s or m/s at full activation"),
    "chassis.pose_limits": ("位姿规划参数", "Pose planner"),
    "chassis.pose_limits.planner_mode": ("规划方式", "Planner", "axis_phase 轴相位 · axis_time 轴时间 · straight 转-直-转",
                                         "axis_phase · axis_time · straight (turn-drive-turn)"),
    "chassis.pose_limits.linear_velocity": ("线速度", "Lin velocity", "m/s，0 = 不限", "m/s, 0 = unlimited"),
    "chassis.pose_limits.linear_acceleration": ("线加速度", "Lin accel", "m/s²，0 = 不限", "m/s², 0 = unlimited"),
    "chassis.pose_limits.linear_deceleration": ("线减速度", "Lin decel", "m/s²，0 = 同加速度", "m/s², 0 = same as acceleration"),
    "chassis.pose_limits.linear_reversal_deceleration": ("线反向减速", "Lin reversal", "m/s²，0 = 同减速度", "m/s², 0 = same as deceleration"),
    "chassis.pose_limits.linear_jerk": ("线加加速度", "Lin jerk", "m/s³，0 = 不限", "m/s³, 0 = unlimited"),
    "chassis.pose_limits.angular_velocity": ("角速度", "Ang velocity", "rad/s，0 = 不限", "rad/s, 0 = unlimited"),
    "chassis.pose_limits.angular_acceleration": ("角加速度", "Ang accel", "rad/s²，0 = 不限", "rad/s², 0 = unlimited"),
    "chassis.pose_limits.angular_deceleration": ("角减速度", "Ang decel", "rad/s²，0 = 同加速度", "rad/s², 0 = same as acceleration"),
    "chassis.pose_limits.angular_reversal_deceleration": ("角反向减速", "Ang reversal", "rad/s²，0 = 同减速度", "rad/s², 0 = same as deceleration"),
    "chassis.pose_limits.angular_jerk": ("角加加速度", "Ang jerk", "rad/s³，0 = 不限", "rad/s³, 0 = unlimited"),
    "mode": ("求解模式", "Mode", "weighted 加权 · lexicographic 严格分层 · layered_independent/coupled 分层",
             "weighted · lexicographic · layered_independent / layered_coupled"),
    "damping": ("阻尼", "Damping", "加到每个加权阶段 H 对角线，> 0", "added to diag(H) of every weighted stage, > 0"),
    "limit_gain": ("限位增益", "Limit gain", "每步可用的到位置限位距离比例，(0, 1]",
                   "fraction of the distance to a position limit usable per step, (0, 1]"),
    "feasibility_tol": ("可行性容差", "Feasibility tol"),
    "check_feasibility": ("检查可行性", "Check feasibility"),
    "hold_retry": ("失败重试", "Hold retry", "阶段失败时缩小恢复量重试，仍失败则保持",
                   "retry a failed stage with scaled recovery demands, then hold"),
    "sync": ("同步", "Synchronization", "axis_phase 轴相位 · axis_time 轴时间", "axis_phase · axis_time"),
    "lead_speed_scale": ("领先速度比例", "Lead speed scale", "0 = 不限", "0 = free"),
    "deceleration": ("减速度", "Deceleration", "0 = 同加速度", "0 = same as acceleration"),
    "reversal_deceleration": ("反向减速度", "Reversal decel", "0 = 同减速度", "0 = same as deceleration"),
    "preference": ("偏好", "Preference", "posture 姿态 · manipulability 可操作度 · joint_limits 远离限位",
                   "posture · manipulability · joint_limits"),
    "projector": ("零空间投影", "Projector"),
    "projector.damping": ("投影阻尼", "Projector damping"),
    "projector.threshold": ("自适应阈值", "Adaptive threshold", "> 0 启用自适应阻尼", "> 0 enables adaptive damping"),
    "translation": ("平移", "Translation"),
    "rotation": ("旋转", "Rotation"),
    "velocity": ("速度", "Velocity"),
    "acceleration": ("加速度", "Acceleration"),
    "jerk": ("加加速度", "Jerk"),
}


def param_text(kind: str | None, path: tuple) -> tuple | None:
    dotted = ".".join(path)
    for key in ((f"{kind}.{dotted}",) if kind else ()) + (dotted, path[-1]):
        if key in PARAMS:
            return PARAMS[key]
    return None


def default_prefs_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "kstone" / "viewer.json"


class Prefs:
    """Small JSON key-value store; path None keeps everything in memory. IO errors are reported, not raised."""

    def __init__(self, path: Path | None):
        self.path = path
        self.values = {}
        self.error = None
        if path is not None and path.is_file():
            try:
                loaded = json.loads(path.read_text())
                if isinstance(loaded, dict):
                    self.values = loaded
            except (OSError, ValueError) as e:
                self.error = f"{path}: {e}"

    def get(self, key: str, default, valid=lambda v: True):
        value = self.values.get(key, default)
        return value if type(value) is type(default) and valid(value) else default

    def set(self, key: str, value) -> None:
        if self.values.get(key) == value:
            return
        self.values[key] = value
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.values, indent=2, sort_keys=True))
            tmp.replace(self.path)
            self.error = None
        except OSError as e:
            self.error = f"{self.path}: {e}"


class Lang:
    def __init__(self, code: str):
        self.code = code if code in LANGUAGES else "zh"

    def __call__(self, key: str, **fields) -> str:
        zh, en = TEXT[key]
        text = zh if self.code == "zh" else en
        return text.format(**fields) if fields else text

    def get(self, key: str, default: str) -> str:
        return self(key) if key in TEXT else default
