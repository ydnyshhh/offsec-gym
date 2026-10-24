# T1.1b trusted application storage isolation

Two fresh A1 instances use the same build and seed but distinct instance IDs, project names, and secrets. The checker hashes logical rows in the six business tables of each instance's SQLite database. It creates a separate `q2_isolation_marker` table in A's disposable database and verifies the marker appears in A, not B, while business table hashes stay fixed. Resetting A must remove the marker and restore its initial logical state. B's logical state and generation must remain unchanged. Both instances must remain healthy.

This is a trusted controller diagnostic. The marker is never available through the agent gateway or benchmark oracle. It establishes separation for this application storage path, not every possible public business mutation.
