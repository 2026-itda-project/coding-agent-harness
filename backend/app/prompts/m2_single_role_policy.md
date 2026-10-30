This run uses policy=single_round_v1. The following replaces legacy role-switch and
unresolved-question behavior. Implement ONE primary user role, no role switch, no X-Role
permission routing, no real accounts. Other actors are sample data/result states, not
additional interactive screens or actions. Preserve every user-approved scope boundary.
The reviewed handoff names selected_role_id and out_of_scope_ids. PRD.roles contains exactly
one ROLE whose description, status and source match that selected Requirement. Map excluded
requirements ONLY into out_of_scope; never silently omit them or implement their features.
Keep all remaining reviewed requirements in the body and retain their exact status/source.
There are no unresolved user decisions: open_issues must be []. AI defaults stay proposed.
Do not ask after generation begins, invent new blockers or turn defaults into confirmed facts.
PRD.policy must be single_round_v1 so these rules reach Spec, Plan and coding Context.
Spec uses only the selected PRD role; http_steps.role_header is empty. The one role's action
boundaries still apply, but role switching or an X-Role header is not an authentication system.
