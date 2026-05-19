import os

from utils.logger_config import logger


def parse_currency_to_float(currency: str) -> float:
    """
    Convert a currency string to a float. The currency string can contain commas
    and may end with 'M' for millions or 'B' for billions.

    Args:
        currency (str): The currency string to be converted.

    Returns:
        float: The numeric value of the currency string.

    Examples:
        >>> parse_currency_to_float("1,234.56")
        1234.56
        >>> parse_currency_to_float("2.5M")
        2500000.0
        >>> parse_currency_to_float("3B")
        3000000000.0
    """
    # Remove commas from the currency string
    currency_str = currency.replace(",", "").replace("(", "").replace(")", "")

    # Check if the last character is 'M' for millions
    if currency_str[-1] == "M":
        # Convert the numeric part to float and multiply by 1,000,000
        return float(currency_str[:-1]) * 1_000_000

    # Check if the last character is 'B' for billions
    elif currency_str[-1] == "B":
        # Convert the numeric part to float and multiply by 1,000,000,000
        return float(currency_str[:-1]) * 1_000_000_000

    # If no suffix, convert the entire string to float
    return float(currency_str)


def parse_key_statistic_results_item_value(
    result_item: dict, key_index: int
) -> float | str:
    """
    Parses the value of a key statistic result item and converts it to a float or string based on its content.

    Args:
        result_item (dict): The dictionary containing the key statistic result item.
        key_index (int): The index of the item in the result_item dictionary.

    Returns:
        float | str: The parsed value as a float or string.

    Example:
        parse_key_statistic_results_item_value(result_item, 0) -> 123.45
        parse_key_statistic_results_item_value(result_item, 1) -> '2023-01-01'

    Raises:
        KeyError: If the specified key_index does not exist in the result_item dictionary.

    Side Effects:
        - Logs the name and value of the item at the debug level.
    """
    try:
        value = result_item[key_index]["fitem"]["value"]
        name = result_item[key_index]["fitem"]["name"]
        logger.debug({name: value})
    except IndexError:
        logger.debug(
            "Missing key statistic item at index %s (available=%s); defaulting to 0.0",
            key_index,
            len(result_item),
        )
        return 0.0

    if name in ["Latest Dividend Ex-Date"]:
        return value

    if value == "-" or value == "":
        return 0.0

    # clean string
    value = value.replace(",", "").replace("(", "").replace(")", "")

    # percentage
    if "%" in value:
        value = value.replace("%", "")
        return float(value) / 100

    # currency type
    if "B" in value or "M" in value:
        return parse_currency_to_float(value)

    return float(value)


def get_column_letter(n):
    """
    Converts a column number to its corresponding letter(s) as used in spreadsheet applications (e.g., Excel).

    Args:
        n (int): The column number (1-based index).

    Returns:
        str: The corresponding column letter(s).

    Example:
        get_column_letter(1) -> 'A'
        get_column_letter(27) -> 'AA'
        get_column_letter(52) -> 'AZ'

    Raises:
        ValueError: If the input is not a positive integer.
    """
    string = ""
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        string = chr(65 + remainder) + string
    return string


def get_sheet_range(num_columns, num_rows):
    """
    Generates the range of cells in a spreadsheet given the number of columns and rows.

    Args:
        num_columns (int): The number of columns in the sheet.
        num_rows (int): The number of rows in the sheet.

    Returns:
        str: The range of cells in the format 'A1:<end_column_letter><num_rows>'.

    Example:
        get_sheet_range(3, 5) -> 'A1:C5'
        get_sheet_range(27, 10) -> 'A1:AA10'

    Raises:
        ValueError: If the number of columns or rows is not a positive integer.
    """
    start_cell = "A1"
    end_column_letter = get_column_letter(num_columns)
    end_cell = f"{end_column_letter}{num_rows}"
    return f"{start_cell}:{end_cell}"


def get_project_root():
    # Get the current file's directory
    current_dir = os.path.dirname(os.path.abspath(__file__))

    # Navigate up to the project root (adjust the number of '..' as needed)
    project_root = os.path.abspath(os.path.join(current_dir, ".."))  # Adjust if deeper

    return project_root
