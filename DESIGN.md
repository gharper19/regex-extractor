regex-extractor: a tool for extracting data fields in heirarchical tree structure via regex and exporting that flattened tree data to csv for data analysis.

## Impementation
```
flag ignore_failed_matches

### Handling failed matches
def parse_final_level(text:str, pattern:str, starting_level:int, regex_pattern_dict:dict, parent_csv):
# outputs list of csv objects ready to be converted into rows, extracted from text using regex provided
	So we are in the final level, we check our first match: 
		is there no matches, return null. On the layer above, check for a level to return null and output that as a list with an empty csv row item with Status Failed Matched and title “No Matches Found”
		Is there text data before it? Make that text a row with status Failed Match.
		Is there text between the end of the span and the next match / or the end of the text-if no addl matches exists? capture that as a row with status failed match
	take current match and parse groups for data. Leave data fields Null when not extracted successfully. 
	*All csv rows have the following fields populated
		- match_Index: <index in regex match list> 
		- match_level: <Hierarchical level>
		- csv_row_index: <row index in csv> 
		- status: <Matched, Failed, Parent>
		- parent: <Higher Level Parent id (level, match_Index)> 
		- parent_match_text: <pattern matched text retrieved from parent regex>
		- ancestor_dict: {
				<level>: (<Ancestor ID(level, match_index)>, <Ancestor Match Text>)
		- inherited_fields_dict: {		# Contains all fields extracted from ancestors, including most recent parent. Maintains group fields extracted from parents to be included in child object
				<group_name>:<Extracted_Value>} 
		- span: <start and end indicies in the text>
		- match_text: <regex text matching pattern> 
		* One of each of the group fields provided in regex pattern
		* One of each of the group fields extracted from each ancestor from each hierarchal level

def parse_heirarchy(text:str, pattern:str, starting_level:int, regex_pattern_dict:dict, parent_csv_object): # Here recent parent is the CsvRow Object that contains all the data we need to populate the children. If we have to go a level deeper, then w
# outputs list of csv Rows ready to be 
	# first validate that the starting_level value exists in the regex_pattern_dict. If not, Return a list containing a single Generic Error CSVRow Object describing the error. 
	
	# We are checking a generic level, We check our first match:
	is this the last level? 
		if so, run parse_final_level(level, pattern, text) and add result to the csv list
		If not, we simply want to flatten this level into the next, then extract that data as the rows, with this parent data as associated properties of that final level csv item. 

	check matches
		is there no matches, return Return a list containing a single Generic Error CSVRow Object describing the error. Status Failed Matched and title “No Matches Found”
	for match in matches: 
		For the first match, Is there text data before it? Make that text a row with status Failed Match.
		Is there text between the end of the span and the next match / or the end of the text-if no addl matches exists? capture that as a row with status failed match and append it to our csv_output
	take current match and parse groups for data. Leave data fields Null when not extracted successfully. Create CSVRow Object using the below fields
	*All csv rows have the following fields populated
		- match_Index: <index in regex match list> 
		- match_level: <Hierarchical level>
		- csv_row_index: <row index in csv>   # Include row index only if Row will not be flattened. If it will, then do not append it to the output csv
		- status: <Matched, Failed, Parent(to be unfolded)>
		- ancestor_dict: {
				<level>: (<Ancestor ID(level, match_index)>, <Ancestor Match Text>)}
		- inherited_fields_dict: {		# Contains all fields extracted from ancestors, including most recent parent. Maintains group fields extracted from parents to be included in child object
				<group_name>:<Extracted_Value>} 
		- parent: <Higher Level Parent id (level, match_Index)> 
		- parent_match_text: <pattern matched text retrieved from parent regex>
		- span: <start and end indicies in the text>
		- match_text: <regex text matching pattern>
		* One of each of the group fields provided in regex pattern, all of which need to be inherited by the child
		* IF Parent Item was provided as input, then use that to inform
	
	# If you encounter a critical error while extracting data, add a generic CSVRow Ojbect detailing the error to your csv_output, then return csv_output.	

	# Once data has been parsed and CSVRow Object has been created for Parent Item, we want to take what rows we get from parsing the match text at subsequent child levels and append those output rows to our current csv_output list. We will not append the parent object we just created, we will just pass it in as an input to the parse function. The output is what we are adding to our csv_output list
		with child_rows as parse_heirarchy(
				text=text, 
				pattern=regex_pattern_dict[str(starting_level+1)],
				starting_level=starting_level+1, 
				regex_pattern_dict=regex_pattern_dict, 
				parent_csv_object=parent_csv_object
			):
			# Check if there are any successful matches with status=Matched. If not, then there was an error in parsing or no results, so change this Parent item’s status to Matched, append it to the csv_output, then append the error rows retrieved from the parse call.
			# if there are matches, then we have successfully flattened this parent item and can add the parse output directly to the csv_output
			if matches_exists: 
				parent_csv_object.status = “Matched”
			csv_output += child_rows
		
		# return csv_output collection once all matches have processed all of their descendants in each lower level and appended them to the collection
		return csv_output

def show_matches():
# Show spans for each match highlighted with Parent data color coded according to parent level. All parent data of the same level should be highlighted the same color. If you run out of colors just start from the begging with the shading multiplied by some ratio within a span.Maybe lookup the name of the color for the legend. 

```

## TODO
- Review: Should matching higher level text only match the target heading? And then pass in the text in between the headings, rather than the match text? --- recc'd because section-capturing patterns need a nested quantifier like `(?:\n[^#]*)+`, which is a catastrophic-backtracking shape that can hang on a near-miss — bad in a UI that re-matches on every keystroke. Header-only patterns are trivial one-liners and the body comes free from the offsets between consecutive headings. -claude
