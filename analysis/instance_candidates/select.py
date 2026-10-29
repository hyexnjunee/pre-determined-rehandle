"""후보 구간 고르기: python select.py hour_windows_all.csv candidates.csv

  A1/A2 목표  GO rehandle ≥ 4 (2건 이상), GO 트럭 대기 중앙값 ≥ 20분, 시작 때 기다리던 GO ≥ 1, SC 가동률 ≤ 0.35, LC 가동률 ≥ 0.8
  B 대조     위와 같되 SC 가동률 ≥ 0.8 이고 SC 본 작업 ≥ 6
  C 대조     GO rehandle 1~4, GO 대기 중앙값 ≤ 12분, SC 가동률 0.3~0.6
  공통       본 작업 5~16개 (MIP 로 풀 수 있는 크기), 블록당 2개 · 날짜당 1개까지
"""
import pandas as pd, sys
d = pd.read_csv(sys.argv[1]); out = sys.argv[2]
d["n_main"] = d.n_go + d.n_gi + d.n_sc_main
size = d.n_main.between(5, 16)
def pick(m, n, key, asc, per_block=2):
    c = d[m & size].sort_values(key, ascending=asc)
    c = c.groupby("block").head(per_block).groupby("day").head(1)
    return c.head(n)
target = (d.go_rh >= 4) & (d.go_rh_jobs >= 2) & (d.go_wait_med_min >= 20) & (d.go_queued_at_start >= 1) & (d.sc_busy <= 0.35) & (d.lc_busy >= 0.8)
both = (d.go_rh >= 4) & (d.go_rh_jobs >= 2) & (d.go_wait_med_min >= 20) & (d.go_queued_at_start >= 1) & (d.sc_busy >= 0.8) & (d.n_sc_main >= 6) & (d.lc_busy >= 0.8)
normal = d.go_rh.between(1, 4) & (d.go_wait_med_min <= 12) & d.sc_busy.between(0.3, 0.6)
print("조건 맞는 구간: 목표", int((target & size).sum()), "/ 둘 다 바쁨", int((both & size).sum()), "/ 평상시", int((normal & size).sum()), "/ 전체", len(d))
t1 = pick(target & (d.n_sc_main >= 2), 12, ["go_rh", "sc_busy"], [False, True]); t1.insert(0, "group", "A1 목표: LC 밀림 + SC 여유 (SC 본 작업 있음)")
t2 = pick(target & (d.n_sc_main <= 1) & ~d.index.isin(t1.index), 8, ["go_rh", "sc_busy"], [False, True]); t2.insert(0, "group", "A2 목표: LC 밀림 + SC 거의 빔")
t = pd.concat([t1, t2])
b = pick(both, 8, ["go_rh"], [False]); b.insert(0, "group", "B 대조: 둘 다 바쁨 (SC 본 작업 6개 이상)")
n = pick(normal, 8, ["go_rh"], [False]); n.insert(0, "group", "C 대조: 평상시")
res = pd.concat([t, b, n]); res.to_csv(out, index=False, encoding="utf-8-sig")
pd.set_option("display.width", 250)
print(res[["group","day","block","start","n_go","n_gi","n_sc_main","go_rh","go_rh_jobs","go_wait_med_min","go_queued_at_start","sc_busy","lc_busy"]].to_string(index=False))
