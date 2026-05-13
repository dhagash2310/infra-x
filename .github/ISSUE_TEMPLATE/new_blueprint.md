---
name: New blueprint request
about: Suggest a new blueprint that should ship with infra-x
labels: blueprint
---

**Blueprint name**
e.g. "AWS RDS Postgres (small, single-AZ)"

**Cloud and resources**
Which provider and roughly which resources should it include? Example:

- Provider: AWS
- Resources: aws_db_instance, aws_db_subnet_group, aws_security_group, aws_secretsmanager_secret

**Who is this for**
What workload or audience does this blueprint serve? Example: "Dev/staging databases for small teams that don't need HA yet."

**Estimated cost / month**
Rough range, e.g. $25–60.

**Any opinions about defaults**
e.g. "instance_class should default to db.t3.micro for cost," "should ship with point-in-time recovery enabled by default."

**Are you willing to author it?**
- [ ] Yes, I'd like to write the YAML and submit a PR
- [ ] No, just suggesting

If yes, see [BLUEPRINT_AUTHORING.md](../../BLUEPRINT_AUTHORING.md) and feel free to ask questions in this issue while you work.
