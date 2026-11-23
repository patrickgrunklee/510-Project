# Assumptions

**Setup time**
* We will setup once workcell set (ie all of station 5) every time the lot size moves through those stations (cumulativly)
* It will lock the required number of operators to work on each station (ie if there is 5 stations - 5 operaotors must work on the reduction so if there is a setup time of 10 min per station (5 stations * 10 min / 5 operators = 10 min) - can change the number if differenet (more or less operators are working))
* This will happen for each product moving through the line - so each of the 3 products can be worked on simeltaneously with no setup times required until the batch/lot moves through the line