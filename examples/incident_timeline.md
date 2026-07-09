# Data Center Incident Timeline

10:00 release-control INFO window=sfo2-maint services=dashboard,auth,billing,mail,search state=open.
10:01 edge-health INFO region=all check=network-public status=green.
10:02 facilities-power CRIT hall=B rail=PDU-B state=dropped affected_shelf=R12-S3 workload=user-kv impact=storage_power_lost.
10:03 net-topology WARN rack=N4 switch=T7 event=link_flap duration=90s affected=metrics-exporters.
10:04 facilities-ups WARN cabinet=B14 mode=bypass affected_shelf=R09-S1 workload=archive.
10:05 facilities-cooling CRIT hall=C loop=C event=trip affected_shelf=H07-S2 workload=kms-hsm temp=overheat.
10:06 facilities-cooling WARN pump=P2 speed=slow affected_rack=G3 workload=recommendation-batch.
10:07 access-control WARN controller=D6 event=reboot location=loading-bay affected=badge-log-upload.
10:08 rack-monitor WARN rack=M2 fan_tray=F5 state=failed workload=mail-queue failover=standby-lane.
10:09 lab-power WARN ups=U8 charge=low affected_rack=L1 action=move-lab-workloads.
10:10 fiber-monitor WARN patch_panel=FP-2 metric=crc_errors affected=analytics-canary.
10:11 dashboard-renderer ERROR path=/home cards=account state=empty upstream=profile-api sessions=18%.
10:12 profile-api ERROR rpc=get_profile_summary store=user-kv shard=kv-17 status=deadline_exceeded.
10:13 user-kv-router INFO shard=kv-17 replica_set=rs-17 placement=sfo2-B/R12-S3.
10:14 user-kv CRIT shard=kv-17 replica_set=rs-17 state=quorum_lost shelf=R12-S3 reason=power_lost.
10:15 link-router ERROR flow=password-reset step=exchange_reset_code status=timeout upstream=token-broker.
10:16 token-broker ERROR action=sign_reset_token status=blocked dependency=kms-proxy-lease-cache.
10:17 kms-proxy INFO cache=lease-cache lease=reset-token-signing placement=sfo2-C/H07-S2.
10:18 kms-proxy CRIT cache=lease-cache state=quorum_lost shelf=H07-S2 reason=hsm_overheat.
10:19 search-indexer WARN job=catalog-refresh retry_source=T7-link-flap user_search=online.
10:20 archive-worker INFO shelf=R09-S1 reason=B14-bypass action=move-writes.
10:21 rec-batch WARN rack=G3 reason=P2-pump-slow metric=latency_high.
10:22 badge-sync INFO controller=D6 status=caught_up after=reboot.
10:23 mail-queue INFO rack=M2 reason=fan_tray_F5_failed route=standby-lane.
10:24 analytics-canary WARN stream=lagging reason=FP-2-crc-errors.
10:25 core-health INFO sql=healthy redis=healthy tls=edge-healthy.
10:26 user-kv INFO shard=kv-17 action=move_replicas from_shelf=R12-S3.
10:27 dashboard-renderer INFO cards=account state=recovered after=user-kv-kv-17-quorum.
10:28 kms-proxy INFO cache=lease-cache state=recovered after=H07-S2-cooled.
10:29 incident-command INFO status=split_events domains=storage,hsm,network,archive,gpu,mail,access,analytics.
