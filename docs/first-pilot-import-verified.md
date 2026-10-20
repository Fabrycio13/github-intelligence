# First remote pilot import — verified

Explicit user authorization: first import of `Fabrycio13/project-mds` for `[2026-10-01T00:00:00Z, 2026-10-19T00:00:00Z)`.

The actual CLI with `--apply` exited 0, status `succeeded`. A separate worker connection read back the exact run, snapshot, report, sources and audit rows:

- Sync: `f531c483-9adf-4edf-9e25-6fca188f7c33`; error code NULL.
- Report: `627498ad-cea9-4d9f-8848-664cb4bbebf8`; complete true within the stated collector coverage.
- Snapshot: `beb65fdc-cd09-498a-960e-011eb51b9f59`; PR 1; payload schema version 1.
- Head SHA: `5fd6686638cdf2b1fa0f6e4390e7c1519668a764`.
- One report source links this run to this snapshot.
- Five audit events: sync_queued, sync_started, snapshot_saved, report_created, sync_finished.
- Export read back from the stored report: `reports/import-f531c483-9adf-4edf-9e25-6fca188f7c33.md`.

Observation time in stored report: `2026-10-20T01:42:14.836698Z` UTC. Snapshot is current, not a historical reconstruction. The chosen window contained no typed events according to the generated report; that does not prove no activity. PR 1's current lifecycle was merged, CI passing at the observed head, approvals observed 0. Deployment, integration and functional validation remained unknown. No GitHub changes, grants, migrations or other repository imports were performed.
