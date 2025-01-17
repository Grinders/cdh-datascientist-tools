# -*- coding: utf-8 -*-
"""
cdhtools: Data Science add-ons for Pega.

Various utilities to access and manipulate data from Pega for purposes
of data analysis, reporting and monitoring.
"""

from typing import List, Union
import pandas as pd
import polars as pl
import os
import zipfile
import re
import numpy as np
import datetime
from io import BytesIO
import urllib.request
import pytz
import requests
import logging


def readDSExport(
    filename: Union[pd.DataFrame, pl.DataFrame, str],
    path: str = ".",
    verbose: bool = True,
    **kwargs,
) -> Union[pd.DataFrame, pl.DataFrame]:
    """Read a Pega dataset export file.
    Can accept either a Pandas DataFrame or one of the following formats:
    - .csv
    - .json
    - .zip (zipped json or CSV)
    - .feather
    - .ipc
    - .parquet

    It automatically infers the default file names for both model data as well as predictor data.
    If you supply either 'modelData' or 'predictorData' as the 'file' argument, it will search for them.
    If you supply the full name of the file in the 'path' directory, it will import that instead.

    Parameters
    ----------
    filename : [pd.DataFrame, pl.DataFrame, str]
        Either a Pandas/Polars DataFrame with the source data (for compatibility),
        or a string, in which case it can either be:
        - The name of the file (if a custom name) or
        - Whether we want to look for 'modelData' or 'predictorData' in the path folder.
    path : str, default = '.'
        The location of the file
    verbose : bool, default = True
        Whether to print out which file will be imported

    Keyword arguments
    -----------------
    return_pl: bool
        Whether to return polars dataframe
        if False, transforms to Pandas
    Any:
        Any arguments to plug into the read_* function from Polars.

    Returns
    -------
    pd.DataFrame | pl.DataFrame
        The read data from the given file

    Examples:
        >>> df = readDSExport(filename = 'modelData', path = './datamart')
        >>> df = readDSExport(filename = 'ModelSnapshot.json', path = 'data/ADMData')

        >>> df = pd.read_csv('file.csv')
        >>> df = readDSExport(filename = df)

    """

    # If a dataframe is supplied directly, we can just return it
    if isinstance(filename, pd.DataFrame) | isinstance(filename, pl.DataFrame):
        logging.debug("Dataframe returned directly")
        return filename

    # If the data is a BytesIO object, such as an uploaded file
    # in certain webapps, then we can simply return the object
    # as is, while extracting the extension as well.
    if isinstance(filename, BytesIO):
        logging.debug("Filename is of type BytesIO, importing that directly")
        name, extension = os.path.splitext(filename.name)
        return import_file(filename, extension, **kwargs)

    # If the filename is simply a string, then we first
    # extract the extension of the file, then look for
    # the file in the user's directory.
    if os.path.isfile(os.path.join(path, filename)):
        logging.debug("File found in directory")
        file = os.path.join(path, filename)
    else:
        logging.debug("File not found in directory, scanning for latest file")
        file = get_latest_file(path, filename)

    # If we can't find the file locally, we can try
    # if the file's a URL. If it is, we need to wrap
    # the file in a BytesIO object, and read the file
    # fully to disk for pyarrow to read it.
    if file == "Target not found" or file == None:
        logging.debug("Could not find file in directory, checking if URL")
        import requests

        try:
            response = requests.get(f"{path}/{filename}")
            logging.info(f"Response: {response}")
            if response.status_code == 200:
                logging.debug("File found online, importing and parsing to BytesIO")
                file = f"{path}/{filename}"
                file = BytesIO(urllib.request.urlopen(file).read())
                name, extension = os.path.splitext(filename)

        except Exception as e:
            logging.info(e)
            if verbose:
                print(f"File {filename} not found in dir {path}")
            logging.info(f"File not found: {path}/{filename}")
            return None

    if "extension" not in vars():
        name, extension = os.path.splitext(file)

    # Now we should either have a full path to a file, or a
    # BytesIO wrapper around the file. Polars can read those both.
    return import_file(file, extension, **kwargs)


def import_file(
    file: str, extension: str, **kwargs
) -> Union[pl.DataFrame, pd.DataFrame]:
    """Imports a file using Polars

    By default, exports to Pandas

    Parameters
    ----------
    File: str
        The path to the file, passed directly to the read functions
    extension: str
        The extension of the file, used to determine which function to use

    Keyword arguments
    -----------------
    return_pl: bool, default = False
        Whether to return Polars or Pandas
        If return_pl is True, then keeps the Polars dataframe

    Returns
    -------
    Union[pd.DataFrame | pl.DataFrame]
        A Pandas or Polars dataframe, depending on `return_pl`
    """

    if extension == ".zip":
        logging.debug("Zip file found, extracting data.json to BytesIO.")
        file, extension = readZippedFile(file)

    if extension == ".csv":
        file = pl.read_csv(
            file,
            sep=kwargs.get("sep", ","),
        )

    elif extension == ".json":
        try:
            if isinstance(file, BytesIO):
                file = pl.read_ndjson(file)
            else:
                file = pl.scan_ndjson(
                    file, infer_schema_length=kwargs.pop("infer_schema_length", 10000)
                ).collect()
        except:
            file = pl.read_json(file)

    elif extension == ".parquet":
        file = pl.read_parquet(file)

    elif (
        extension == ".feather"
        or extension.casefold() == ".ipc"
        or extension.casefold() == ".arrow"
    ):
        file = pl.read_ipc(file)

    else:
        raise ValueError(f"Could not import file: {file}, with extension {extension}")

    if kwargs.pop("return_pl", False):
        return file
    else:
        return file.to_pandas()


def readZippedFile(file: str, verbose: bool = False, **kwargs) -> BytesIO:
    """Read a zipped NDJSON file.
    Reads a dataset export file as exported and downloaded from Pega. The export
    file is formatted as a zipped multi-line JSON file. It reads the file,
    and then returns the file as a BytesIO object.

    Parameters
    ----------
    file : str
        The full path to the file
    verbose : str, default=False
        Whether to print the names of the files within the unzipped file for debugging purposes

    Returns
    -------
    os.BytesIO
        A Polars dataframe with the contents.
    """
    with zipfile.ZipFile(file, mode="r") as z:
        logging.debug("Opened zip file.")
        files = z.namelist()
        logging.debug(f"Files found: {files}")
        if "data.json" in files:
            logging.debug("data.json found.")
            with z.open("data.json") as zippedfile:
                return (BytesIO(zippedfile.read()), ".json")
        else:  # pragma: no cover
            raise FileNotFoundError("Cannot find a 'data' file in the zip folder.")


def get_latest_file(path: str, target: str, verbose: bool = False) -> str:
    """Convenience method to find the latest model snapshot.
    It has a set of default names to search for and finds all files who match it.
    Once it finds all matching files in the directory, it chooses the most recent one.
    It only looks at .json, .csv and .zip files for now, as they are supported.
    Needs a path to the directory and a target of either 'modelData' or 'predictorData'.

    Parameters
    ----------
    path : str
        The filepath where the data is stored
    target : str in ['modelData', 'predictorData']
        Whether to look for data about the predictive models ('modelData')
        or the predictor bins ('predictorData')
    verbose : bool, default = False
        Whether to print all found files before comparing name criteria for debugging purposes

    Returns
    -------
    str
        The most recent file given the file name criteria.
    """
    if target not in {"modelData", "predictorData", "ValueFinder"}:
        return f"Target not found"

    supported = [".json", ".csv", ".zip", ".parquet", ".feather", ".ipc"]

    files_dir = [f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]
    files_dir = [f for f in files_dir if os.path.splitext(f)[-1].lower() in supported]
    if verbose:
        print(files_dir)  # pragma: no cover
    matches = getMatches(files_dir, target)

    if len(matches) == 0:
        if verbose:  # pragma: no cover
            print(
                f"Unable to find data for {target}. Please check if the data is available."
            )
        return None

    paths = [os.path.join(path, name) for name in matches]

    def f(x):
        try:
            return fromPRPCDateTime(re.search("\d.*GMT", x)[0].replace("_", " "))
        except:
            return pytz.timezone("GMT").localize(
                datetime.datetime.fromtimestamp(os.path.getctime(x))
            )

    dates = np.array([f(i) for i in paths])
    return paths[np.argmax(dates)]


def getMatches(files_dir, target):
    matches = []
    default_model_names = [
        "Data-Decision-ADM-ModelSnapshot",
        "PR_DATA_DM_ADMMART_MDL_FACT",
        "model_snapshots",
        "MD_FACT",
        "ADMMART_MDL_FACT_Data",
        "cached_modelData",
    ]
    default_predictor_names = [
        "Data-Decision-ADM-PredictorBinningSnapshot",
        "PR_DATA_DM_ADMMART_PRED",
        "predictor_binning_snapshots",
        "PRED_FACT",
        "cached_predictorData",
    ]
    ValueFinder_names = ["Data-Insights_pyValueFinder", "cached_ValueFinder"]

    if target == "modelData":
        names = default_model_names
    elif target == "predictorData":
        names = default_predictor_names
    elif target == "ValueFinder":
        names = ValueFinder_names
    for file in files_dir:
        match = [file for name in names if re.findall(name.casefold(), file.casefold())]
        if len(match) > 0:
            matches.append(match[0])
    return matches


def cache_to_file(
    df: pd.DataFrame,
    path: os.PathLike,
    name: str,
    cache_type: str = "ipc",
    compression: str = "uncompressed",
) -> os.PathLike:
    """Very simple convenience function to cache data.
    Caches in parquet format for very fast reading.

    Parameters
    ----------
    df: pd.DataFrame | pl.DataFrame
        The dataframe to cache
    path: os.PathLike
        The location to cache the data
    name: str
        The name to give to the file
    cache_type: str
        The type of file to export.
        Default is IPC, also supports parquet
    compression: str
        The compression to apply, default is uncompressed

    Returns
    -------
    os.PathLike:
        The filepath to the cached file
    """
    outpath = f"{path}/{name}"
    if isinstance(df, pd.DataFrame):
        df = pl.DataFrame(df)
    if cache_type == "ipc":
        outpath = f"{outpath}.arrow"
        df.write_ipc(outpath, compression=compression)
    if cache_type == "parquet":
        outpath = f"{outpath}.parquet"
        df.write_parquet(outpath, compression=compression)
    return outpath


def defaultPredictorCategorization(x: str) -> str:
    return x.split(".")[0] if len(x.split(".")) > 1 else "Primary"


def safe_range_auc(auc: float) -> float:
    """Internal helper to keep auc a safe number between 0.5 and 1.0 always.

    Parameters
    ----------
    auc : float
        The AUC (Area Under the Curve) score

    Returns
    -------
    float
        'Safe' AUC score, between 0.5 and 1.0
    """

    if np.isnan(auc):
        return 0.5
    else:
        return 0.5 + np.abs(0.5 - auc)


def auc_from_probs(
    groundtruth: List[int], probs: List[float]
) -> List[float]:  # pragma: no cover
    """Calculates AUC from an array of truth values and predictions.
    Calculates the area under the ROC curve from an array of truth values and
    predictions, making sure to always return a value between 0.5 and 1.0 and
    returns 0.5 when there is just one groundtruth label.

    Parameters
    ----------
    groundtruth : List[int]
        The 'true' values, Positive values must be represented as
        True or 1. Negative values must be represented as False or 0.
    probs : List[float]
        The predictions, as a numeric vector of the same length as groundtruth

    Returns : List[float]
        The AUC as a value between 0.5 and 1.

    Examples:
        >>> auc_from_probs( [1,1,0], [0.6,0.2,0.2])
    """
    nlabels = len(np.unique(groundtruth))
    if nlabels < 2:
        return 0.5
    if nlabels > 2:
        raise Exception("'Groundtruth' has more than two levels.")

    df = pl.DataFrame({"truth": groundtruth, "probs": probs})
    binned = df.groupby(by="probs").agg(
        [
            (pl.col("truth") == 1).sum().alias("pos"),
            (pl.col("truth") == 0).sum().alias("neg"),
        ]
    )

    return auc_from_bincounts(
        binned.get_column("pos"), binned.get_column("neg"), binned.get_column("probs")
    )


def auc_from_bincounts(
    pos: List[int], neg: List[int], probs: List[float] = None
) -> float:
    """Calculates AUC from counts of positives and negatives directly
    This is an efficient calculation of the area under the ROC curve directly from an array of positives
    and negatives. It makes sure to always return a value between 0.5 and 1.0
    and will return 0.5 when there is just one groundtruth label.

    Parameters
    ----------
    pos : List[int]
        Vector with counts of the positive responses
    neg: List[int]
        Vector with counts of the negative responses
    probs: List[float]
        Optional list with probabilities which will be used to set the order of the bins. If missing defaults to pos/(pos+neg).

    Returns
    -------
    float
        The AUC as a value between 0.5 and 1.

    Examples:
        >>> auc_from_bincounts([3,1,0], [2,0,1])
    """
    pos = np.asarray(pos)
    neg = np.asarray(neg)
    if probs is None:
        o = np.argsort(-(pos / (pos + neg)))
    else:
        o = np.argsort(-np.asarray(probs))
    FPR = np.flip(np.cumsum(neg[o]) / np.sum(neg), axis=0)
    TPR = np.flip(np.cumsum(pos[o]) / np.sum(pos), axis=0)
    Area = (FPR - np.append(FPR[1:], 0)) * (TPR + np.append(TPR[1:], 0)) / 2
    return safe_range_auc(np.sum(Area))


def aucpr_from_probs(
    groundtruth: List[int], probs: List[float]
) -> List[float]:  # pragma: no cover
    """Calculates PR AUC (precision-recall) from an array of truth values and predictions.
    Calculates the area under the PR curve from an array of truth values and
    predictions. Returns 0.0 when there is just one groundtruth label.

    Parameters
    ----------
    groundtruth : List[int]
        The 'true' values, Positive values must be represented as
        True or 1. Negative values must be represented as False or 0.
    probs : List[float]
        The predictions, as a numeric vector of the same length as groundtruth

    Returns : List[float]
        The AUC as a value between 0.5 and 1.

    Examples:
        >>> auc_from_probs( [1,1,0], [0.6,0.2,0.2])
    """
    nlabels = len(np.unique(groundtruth))
    if nlabels < 2:
        return 0.0
    if nlabels > 2:
        raise Exception("'Groundtruth' has more than two levels.")

    df = pl.DataFrame({"truth": groundtruth, "probs": probs})
    binned = df.groupby(by="probs").agg(
        [
            (pl.col("truth") == 1).sum().alias("pos"),
            (pl.col("truth") == 0).sum().alias("neg"),
        ]
    )

    return aucpr_from_bincounts(
        binned.get_column("pos"), binned.get_column("neg"), binned.get_column("probs")
    )


def aucpr_from_bincounts(
    pos: List[int], neg: List[int], probs: List[float] = None
) -> float:
    """Calculates PR AUC (precision-recall) from counts of positives and negatives directly.
    This is an efficient calculation of the area under the PR curve directly from an
    array of positives and negatives. Returns 0.0 when there is just one
    groundtruth label.

    Parameters
    ----------
    pos : List[int]
        Vector with counts of the positive responses
    neg: List[int]
        Vector with counts of the negative responses
    probs: List[float]
        Optional list with probabilities which will be used to set the order of the bins. If missing defaults to pos/(pos+neg).

    Returns
    -------
    float
        The PR AUC as a value between 0.0 and 1.

    Examples:
        >>> aucpr_from_bincounts([3,1,0], [2,0,1])
    """
    pos = np.asarray(pos)
    neg = np.asarray(neg)
    if probs is None:
        o = np.argsort(-(pos / (pos + neg)))
    else:
        o = np.argsort(-np.asarray(probs))
    recall = np.cumsum(pos[o]) / np.sum(pos)
    precision = np.cumsum(pos[o]) / np.cumsum(pos[o] + neg[o])
    prevrecall = np.insert(recall[0 : (len(recall) - 1)], 0, 0)
    prevprecision = np.insert(precision[0 : (len(precision) - 1)], 0, 0)
    Area = (recall - prevrecall) * (precision + prevprecision) / 2
    return np.sum(Area[1:])


def auc2GINI(auc: float) -> float:
    """
    Convert AUC performance metric to GINI

    Parameters
    ----------
    auc: float
        The AUC (number between 0.5 and 1)

    Returns
    -------
    float
        GINI metric, a number between 0 and 1

    Examples:
        >>> auc2GINI(0.8232)
    """
    return 2 * safe_range_auc(auc) - 1


def _capitalize(fields: list) -> list:
    """Applies automatic capitalization, aligned with the R couterpart.

    Parameters
    ----------
    fields : list
        A list of names

    Returns
    -------
    fields : list
        The input list, but each value properly capitalized
    """
    capitalizeEndWords = [
        "ID",
        "Key",
        "Name",
        "Treatment",
        "Count",
        "Category",
        "Class",
        "Time",
        "DateTime",
        "UpdateTime",
        "ToClass",
        "Version",
        "Predictor",
        "Predictors",
        "Rate",
        "Ratio",
        "Negatives",
        "Positives",
        "Threshold",
        "Error",
        "Importance",
        "Type",
        "Percentage",
        "Index",
        "Symbol",
        "LowerBound",
        "UpperBound",
        "Bins",
        "GroupIndex",
        "ResponseCount",
        "NegativesPercentage",
        "PositivesPercentage",
        "BinPositives",
        "BinNegatives",
        "BinResponseCount",
        "BinSymbol",
        "ResponseCountPercentage",
        "ConfigurationName",
        "Configuration",
    ]
    fields = [re.sub("^p(x|y|z)", "", field.lower()) for field in fields]
    for word in capitalizeEndWords:
        fields = [re.sub(word, word, field, flags=re.I) for field in fields]
        fields = [field[:1].upper() + field[1:] for field in fields]
    return fields


def fromPRPCDateTime(
    x: str, return_string: bool = False
) -> Union[datetime.datetime, str]:
    """Convert from a Pega date-time string.

    Parameters
    ----------
    x: str
        String of Pega date-time
    return_string: bool, default=False
        If True it will return the date in string format. If
        False it will return in datetime type

    Returns
    -------
    Union[datetime.datetime, str]
        The converted date in datetime format or string.

    Examples:
        >>> fromPRPCDateTime("20180316T134127.847 GMT")
        >>> fromPRPCDateTime("20180316T134127.847 GMT", True)
        >>> fromPRPCDateTime("20180316T184127.846")
        >>> fromPRPCDateTime("20180316T184127.846", True)
    """

    timezonesplits = x.split(" ")

    if len(timezonesplits) > 1:
        x = timezonesplits[0]

    if "." in x:
        date_no_frac, frac_sec = x.split(".")
        # TODO: obtain only 3 decimals
        if len(frac_sec) > 3:
            frac_sec = frac_sec[:3]
        elif len(frac_sec) < 3:
            frac_sec = "{:<03d}".format(int(frac_sec))
    else:
        date_no_frac = x

    dt = datetime.datetime.strptime(date_no_frac, "%Y%m%dT%H%M%S")

    if len(timezonesplits) > 1:
        dt = dt.replace(tzinfo=pytz.timezone(timezonesplits[1]))

    if "." in x:
        dt = dt.replace(microsecond=int(frac_sec))

    if return_string:
        return dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    else:
        return dt


def toPRPCDateTime(x: datetime.datetime) -> str:
    """Convert to a Pega date-time string

    Parameters
    ----------
    x: datetime.datetime
        A datetime object

    Returns
    -------
    str
        A string representation in the format used by Pega

    Examples:
        >>> toPRPCDateTime(datetime.datetime.now())
    """

    return x.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def weighed_average_polars(vals, weights) -> pl.Expr:
    return ((pl.col(vals) * pl.col(weights)).sum()) / pl.col(weights).sum()


def weighed_performance_polars() -> pl.Expr:
    """Polars function to return a weighted performance"""
    return weighed_average_polars("Performance", "ResponseCount")


def readClientCredentialFile(credentialFile):
    outputdict = {}
    with open(credentialFile) as f:
        for idx, line in enumerate(f.readlines()):
            if (idx % 2) == 0:
                key = line.rstrip("\n")
            else:
                outputdict[key] = line.rstrip("\n")
        return outputdict


def getToken(credentialFile, verify=True, **kwargs):
    creds = readClientCredentialFile(credentialFile)
    return requests.post(
        url=kwargs.get("URL", creds["Access token endpoint"]),
        data={"grant_type": "client_credentials"},
        auth=(creds["Client ID"], creds["Client Secret"]),
        verify=verify,
    ).json()["access_token"]
