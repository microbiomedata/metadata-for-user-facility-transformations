import calendar
import os
import json

import pandas as pd
import click
import requests

from typing import Dict, Any, List, Union
from dotenv import load_dotenv, dotenv_values


class MetadataRetriever:
    """
    Retrieves metadata records from a given submission ID and user facility.
    """

    USER_FACILITY_DICT: Dict[str, str] = {
        "emsl": "emsl_data",
        "jgi_mg": "jgi_mg_data",
        "jgi_mt": "jgi_mt_data",
    }

    def __init__(self, metadata_submission_id: str, user_facility: str) -> None:
        """
        Initialize the MetadataRetriever.

        :param metadata_submission_id: The ID of the metadata submission.
        :param user_facility: The user facility to retrieve data from.
        """
        self.metadata_submission_id = metadata_submission_id
        self.user_facility = user_facility
        self.load_and_set_env_vars()
        self.base_url = self.env.get("SUBMISSION_PORTAL_BASE_URL")

    def load_and_set_env_vars(self):
        """Loads and sets environment variables from .env file."""
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        env_vars = dotenv_values(env_path)
        for key, value in env_vars.items():
            os.environ[key] = value

        self.env: Dict[str, str] = dict(os.environ)

    def retrieve_metadata_records(self, unique_field: str) -> pd.DataFrame:
        """
        Retrieves the metadata records for the given submission ID and user facility.

        :return: The retrieved metadata records as a Pandas DataFrame.
        """
        self.load_and_set_env_vars()

        refresh_response = requests.post(
            f"{self.base_url}/auth/refresh",
            json={"refresh_token": self.env["DATA_PORTAL_REFRESH_TOKEN"]},
        )
        refresh_response.raise_for_status()
        refresh_body = refresh_response.json()
        access_token = refresh_body["access_token"]

        headers = {
            "content-type": "application/json; charset=UTF-8",
            "Authorization": f"Bearer {access_token}",
        }
        response: Dict[str, Any] = requests.get(
            f"{self.base_url}/api/metadata_submission/{self.metadata_submission_id}",
            headers=headers,
        ).json()

        # Get user-facility key data
        common_df: pd.DataFrame = pd.DataFrame()
        if self.user_facility in self.USER_FACILITY_DICT:
            user_facility_data: Dict[str, Any] = response["metadata_submission"][
                "sampleData"
            ].get(self.USER_FACILITY_DICT[self.user_facility], {})
            common_df = pd.DataFrame(user_facility_data)

        # Check if common_df is empty
        if common_df.empty:
            raise ValueError(
                f"No key {self.user_facility} exists in submission metadata record {self.metadata_submission_id}"
            )
        else:
            df = common_df

        # Find non-user-facility keys (ie, plant_associated, water, etc)
        all_keys_data = response["metadata_submission"]["sampleData"]
        user_facility_keys = ["emsl_data", "jgi_mg_data", "jgi_mt_data"]
        sample_data_keys = [
            key for key in all_keys_data if key not in user_facility_keys
        ]

        # Loop through resulting keys and combine with common_df by samp_name
        for key in sample_data_keys:

            sample_data: Dict[str, Any] = response["metadata_submission"][
                "sampleData"
            ].get(key, {})
            sample_data_df = pd.DataFrame(sample_data)

            if not sample_data_df.empty:
                df = pd.merge(df, sample_data_df, on="samp_name", how="left")

            # Append the non-UF key name into the df for 'Sample Isolated From' col in jgi mg/mt
            df['sample_isolated_from'] = key

        # Begin collecting detailed sample data

        if "lat_lon" in df.columns:
            df[["latitude", "longitude"]] = df["lat_lon"].str.split(" ", expand=True)

        if "depth" in df.columns:
            # Case - different delimiters used
            df["depth"] = df["depth"].str.replace("-", " - ")
            # Case - only one value provided for depth (single value will be max and min)
            dfNew = df["depth"].str.split(" - ", expand=True)
            if dfNew.shape[0] == 1:
                df[["minimum_depth"]] = dfNew[0]
                df[["maximum_depth"]] = dfNew[0]
            else:
                df[["minimum_depth", "maximum_depth"]] = df["depth"].str.split(
                    " - ", expand=True
                )

        if "geo_loc_name" in df.columns:
            df["country_name"] = df["geo_loc_name"].str.split(":").str[0]

        if "collection_date" in df.columns:
            df["collection_year"] = df["collection_date"].str.split("-").str[0]
            df["collection_month"] = df["collection_date"].str.split("-").str[1]
            df["collection_day"] = df["collection_date"].str.split("-").str[2]

            df["collection_month_name"] = df["collection_month"].apply(
                lambda x: calendar.month_name[int(x)]
            )

        # Address 'Was sample DNAse treated?' col
        # Change from 'yes/no' to 'Y/N'
        if self.user_facility == 'jgi_mg':
            df.loc[df["dna_dnase"] == "yes", "dna_dnase"] = 'Y'
            df.loc[df["dna_dnase"] == "no", "dna_dnase"] = 'N'
        if self.user_facility == 'jgi_mt':
            df.loc[df["dnase_rna"] == "yes", "dnase_rna"] = 'Y'
            df.loc[df["dnase_rna"] == "no", "dnase_rna"] = 'N'

        return df


class SpreadsheetCreator:
    """
    Creates a spreadsheet based on a JSON mapper and metadata DataFrame.
    """

    def __init__(
        self,
        json_mapper: Dict[str, Dict[str, Union[str, List[str]]]],
        metadata_df: pd.DataFrame,
    ) -> None:
        """
        Initialize the SpreadsheetCreator.

        :param json_mapper: The JSON mapper specifying column mappings.
        :param metadata_df: The metadata DataFrame to create the spreadsheet from.
        """
        self.json_mapper = json_mapper
        self.metadata_df = metadata_df

    def combine_headers_df(self, header: bool) -> pd.DataFrame:
        """
        Combines and formats the headers DataFrame.

        :param header: True if the headers should be included, False otherwise.
        :return: The combined headers DataFrame.
        """
        d: Dict[str, List[Union[str, List[str]]]] = {}
        for k, v in self.json_mapper.items():
            l: List[Union[str, List[str]]] = [
                h for h_n, h in v.items() if h_n != "sub_port_mapping"
            ]
            d[k] = l

        headers_df: pd.DataFrame = pd.DataFrame(d)

        if header:
            last_row = headers_df.iloc[-1]
            column_values: List[str] = list(last_row)

            headers_df = headers_df.drop(headers_df.index[-1])
            headers_df.loc[len(headers_df)] = headers_df.columns.to_list()
            headers_df.columns = column_values

            shift = 1
            headers_df = pd.concat(
                [headers_df.iloc[-shift:], headers_df.iloc[:-shift]], ignore_index=True
            )

        return headers_df

    def combine_sample_rows_df(self) -> pd.DataFrame:
        """
        Combines and formats the sample rows DataFrame.

        :return: The combined sample rows DataFrame.
        """
        rows_df: pd.DataFrame = pd.DataFrame()
        for k, v in self.json_mapper.items():
            if (
                "sub_port_mapping" in v
                and v["sub_port_mapping"] in self.metadata_df.columns.to_list()
            ):
                if "header" in v:
                    rows_df[v["header"]] = self.metadata_df[v["sub_port_mapping"]]
                else:
                    rows_df[k] = self.metadata_df[v["sub_port_mapping"]]

        return rows_df

    def combine_headers_and_rows(
        self, headers_df: pd.DataFrame, rows_df: pd.DataFrame
    ) -> pd.DataFrame:
        """
        Combines the headers and sample rows DataFrames.

        :param headers_df: The headers DataFrame.
        :param rows_df: The sample rows DataFrame.
        :return: The combined DataFrame.
        """
        return pd.concat([headers_df, rows_df])

    def create_spreadsheet(self, header: bool) -> pd.DataFrame:
        """
        Creates the spreadsheet based on the JSON mapper and metadata DataFrame.

        :param header: True if the headers should be included, False otherwise.
        :return: The created spreadsheet.
        """
        headers_df = self.combine_headers_df(header)
        rows_df = self.combine_sample_rows_df()
        spreadsheet = self.combine_headers_and_rows(headers_df, rows_df)
        return spreadsheet


@click.command()
@click.option("--submission", "-s", required=True, help="Metadata submission id.")
@click.option(
    "--user-facility", "-u", required=True, help="User facility to send data to."
)
@click.option("--header/--no-header", "-h", default=False, show_default=True)
@click.option(
    "--mapper",
    "-m",
    required=True,
    type=click.Path(exists=True),
    help="Path to user facility specific JSON file.",
)
@click.option(
    "--unique-field",
    "-uf",
    required=True,
    help="Unique field to identify the metadata records.",
)
@click.option(
    "--output",
    "-o",
    required=True,
    help="Path to result output XLSX file.",
)
def cli(
    submission: str,
    user_facility: str,
    header: bool,
    mapper: str,
    unique_field: str,
    output: str,
) -> None:
    """
    Command-line interface for creating a spreadsheet based on metadata records.

    :param submission: The ID of the metadata submission.
    :param user_facility: The user facility to retrieve data from.
    :param header: True if the headers should be included, False otherwise.
    :param mapper: Path to the JSON mapper specifying column mappings.
    :param unique_field: Unique field to identify the metadata records.
    :param output: Path to the output XLSX file.
    """
    load_dotenv()
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    env_vars = dotenv_values(env_path)
    for key, value in env_vars.items():
        os.environ[key] = value

    metadata_retriever = MetadataRetriever(submission, user_facility)
    metadata_df = metadata_retriever.retrieve_metadata_records(unique_field)

    with open(mapper, "r") as f:
        json_mapper: Dict[str, Dict[str, Union[str, List[str]]]] = json.load(f)

    spreadsheet_creator = SpreadsheetCreator(json_mapper, metadata_df)
    user_facility_spreadsheet = spreadsheet_creator.create_spreadsheet(header)
    user_facility_spreadsheet.to_excel(output, index=False)


if __name__ == "__main__":
    cli()
