# T1.1b local canary network experiment

The test creates an internal, uniquely named, owner-labeled Docker network and a listener with no published port. A disposable control container on that network must receive an acknowledgement. The test then runs a bounded Python socket attempt in the actual A1 SaaS container namespace against the canary's private address. It requires a completed probe, a blocked result, and an unchanged listener connection count. Docker inspection independently verifies that range containers have exactly their own internal Compose network, no published ports, and no attachment to the canary network.

The conclusion is limited to the observed local canary path and inspected attachments. It is not a universal proof of every possible external route. If the control fails, the probe does not execute, or the listener observes a connection, the active isolation check cannot pass. Cleanup is owner checked.
