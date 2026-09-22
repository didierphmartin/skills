Before starting, confirm these integrations are connected and if they are not then ask the user what integrations would they like to use this playbook with

Title: Slack Channel & Group Management

Trigger: Requester asks to create, archive, modify visibility, or manage membership of a Slack  channel or usergroup.

Instructions: 
1. Parse: action (create / archive / unarchive / change visibility / add members / remove members / rename / set manager), target channel name, member list, visibility setting.
2. Validate name against policy:
2.1 Channels must match ^(launch|project|incident|team|all|fun|help|wg)-[a-z0-9-]+$.
2.2 Length ≤ 80 chars.
2.3 If invalid → #Send Direct Message explaining the rule.
3. #Lookup Users on the requester.
4. Authorization check:
- Create → any employee.
- Archive / rename / change visibility → requester must be a channel manager (#Get Channel Managers) or IT admin.
- Private channel operations → require #Request Approval from a channel manager.
5. #Search Channels to check if the channel already exists.
6. Execute the action:
- Create → #Create Channel with parsed visibility. Then #Set Channel Manager to the requester. #Set Custom Retention to policy default.
- Add members → if a list, #Lookup Users to resolve, then #Add Users To Channel. If Okta group sync requested, #Get Group Members then #Add Users To Channel.
- Remove members → #Remove User From Channel for each.
- Archive → #Archive Channel.
- Unarchive → #Unarchive Channel.
- Change visibility → #Change Channel Visibility.
- Rename → #Rename Channel.
- Set manager → #Set Channel Manager.
7. If the request mentions pinning a doc: #Slack Pin Message action with the doc URL.
8. #Send Direct Message confirming the change with the channel link.
9. #Leave Internal Note capturing action, channel, members.
10. #Resolve Request.

Tools used: Okta; Slack

Actions used: #Send Direct Message; #Lookup Users; #Get Channel Managers; #Request Approval; #Search Channels; #Create Channel; #Set Channel Manager; #Set Custom Retention; #Add Users To Channel; #Get Group Members; #Remove User From Channel; #Archive Channel; #Unarchive Channel; #Change Channel Visibility; #Rename Channel; #Leave Internal Note; #Resolve Request