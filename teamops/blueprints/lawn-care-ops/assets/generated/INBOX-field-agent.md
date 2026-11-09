# INBOX - field-agent (R2)

Drop file for the weekly inspection agent.

- The dispatcher (R1) drops work-order assignments here via edge E1.
- The field agent reads this file, performs the weekly inspection, and writes the ops report and signals into INBOX-boss.md via edge E2.
