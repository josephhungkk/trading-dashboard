from prometheus_client import Counter, Gauge

shadow_promoter_created_total = Counter("shadow_promoter_created_total", "Shadow bots created")
shadow_promoter_promoted_total = Counter(
    "shadow_promoter_promoted_total", "Shadow promotions completed"
)
shadow_promoter_promote_failures_total = Counter(
    "shadow_promoter_promote_failures_total", "Shadow promotion failures"
)
shadow_promoter_comparison_notify_total = Counter(
    "shadow_promoter_comparison_notify_total", "Shadow comparison notifications sent"
)
shadow_promoter_active_shadows = Gauge(
    "shadow_promoter_active_shadows", "Active shadow bots currently running"
)
