This run uses policy=single_round_v1. The following replaces legacy role-switch and
unresolved-question behavior. Implement ONE primary user role, no role switch, no X-Role
permission routing, no real accounts. Other actors are sample data/result states, not
additional interactive screens or actions. Preserve every user-approved scope boundary.
The reviewed handoff names selected_role_id and out_of_scope_ids. PRD.roles contains exactly
one ROLE whose description, status and source match that selected Requirement EXACTLY.
Copy the selected Requirement.description verbatim, character for character. Do not append
its actions, scope, primary-role explanation or any other sentence to ROLE.description.
Put those details in user_flows, functional_requirements or in_scope instead.
Map excluded
requirements ONLY into out_of_scope; never silently omit them or implement their features.
Keep all remaining reviewed requirements in the body and retain their exact status/source.
For PRD items that reference requirements, out_of_scope may reference ONLY IDs listed in
reviewed.out_of_scope_ids. A reviewed requirement can itself be a prohibition ("do not build X").
If its ID is not in that list, preserve the prohibition as an active product constraint in
nfrs or business_rules, with its original status/source and requirement_ids. This does NOT
authorize implementing X. Do not move such a constraint to out_of_scope or drop its trace link.
There are no unresolved user decisions: open_issues must be []. AI defaults stay proposed.
Do not ask after generation begins, invent new blockers or turn defaults into confirmed facts.
PRD.policy must be single_round_v1 so these rules reach Spec, Plan and coding Context.
Spec uses only the selected PRD role; http_steps.role_header is empty. The one role's action
boundaries still apply, but role switching or an X-Role header is not an authentication system.
