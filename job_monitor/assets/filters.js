dagfuncs.cleanDateColumn = (cellValue) => {
    if (cellValue === "NaT") {
        returnValue = "";
    } else {
        returnValue = cellValue.includes("T") ? cellValue.split("T")[0] : cellValue;
    }
    return returnValue;
};

dagfuncs.DateComparator = (filterLocalDateAtMidnight, cellValue) => {
    // converts timestamp to just date for easier user filtering
    const dateAsString = cellValue ? dagfuncs.cleanDateColumn(cellValue) : null;
    if (dateAsString == null) {
        return 0;
    }

    // convert date string to date
    var tempDate = new Date(dateAsString);

    // convert dates to disregard timezones
    var userTimezoneOffset = tempDate.getTimezoneOffset() * 60000;
    const cellDate = new Date(tempDate.getTime() + userTimezoneOffset);

    // Now that both parameters are Date objects, we can compare
    if (cellDate < filterLocalDateAtMidnight) {
        return -1;
    } else if (cellDate > filterLocalDateAtMidnight) {
        return 1;
    }
    return 0;
};
