# Test defects

Map changed behavior to actual test stimuli. Check assertions do not already hold
before the action, both sides of interfaces are exercised, failure paths are reached,
waits have deadlines, and cleanup survives failed assertions/exceptions. Treat missing
deadlines and skipped cleanup as distinct mechanisms. Inspect build/configuration
coverage claimed by changed tests and comments. Missing tests alone is not a defect
without a claimed or mandatory coverage obligation or a demonstrable wrong assertion.
