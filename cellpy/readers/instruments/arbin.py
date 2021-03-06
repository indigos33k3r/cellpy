"""arbin res-type data files"""
import os
import tempfile
import shutil
import logging
import platform
import warnings
import numpy as np

import pandas as pd

from cellpy.readers.core import FileID, DataSet, check64bit, humanize_bytes
from cellpy.parameters.internal_settings import get_headers_normal
from cellpy.readers.instruments.mixin import Loader
from cellpy.parameters import prms

# Select odbc module
ODBC = prms._odbc
SEARCH_FOR_ODBC_DRIVERS = prms._search_for_odbc_driver

use_subprocess = prms.Instruments.use_subprocess
detect_subprocess_need = prms.Instruments.detect_subprocess_need

# Finding out some stuff about the platform
is_posix = False
is_macos = False
if os.name == "posix":
    is_posix = True
current_platform = platform.system()
if current_platform == "Darwin":
    is_macos = True

if detect_subprocess_need:
    python_version, os_version = platform.architecture()
    if python_version == "64bit" and prms.Instruments.office_version == "32bit":
        use_subprocess = True

if use_subprocess and not is_posix:
    # The windows users most likely have a strange custom path to mdbtools etc.
    if not prms.Instruments.sub_process_path:
        sub_process_path = str(prms._sub_process_path)
    else:
        sub_process_path = str(prms.Instruments.sub_process_path)

if is_posix:
    sub_process_path = "mdb-export"

try:
    DRIVER = prms.odbc_driver
except AttributeError:
    DRIVER = "NO DRIVER"
use_ado = False

if ODBC == "ado":
    use_ado = True
    try:
        import adodbapi as dbloader  # http://adodbapi.sourceforge.net/
    except ImportError:
        use_ado = False

if not use_ado:
    if ODBC == "pyodbc":
        try:
            import pyodbc as dbloader
        except ImportError:
            warnings.warn("COULD NOT LOAD DBLOADER!", ImportWarning)
            dbloader = None
    elif ODBC == "pypyodbc":
        try:
            import pypyodbc as dbloader
        except ImportError:
            warnings.warn("COULD NOT LOAD DBLOADER!", ImportWarning)
            dbloader = None

# The columns to choose if minimum selection is selected
MINIMUM_SELECTION = ["Data_Point", "Test_Time", "Step_Time", "DateTime", "Step_Index", "Cycle_Index",
                     "Current", "Voltage", "Charge_Capacity", "Discharge_Capacity", "Internal_Resistance"]

# Names of the tables in the .res db that is used by cellpy
TABLE_NAMES = {
    "normal": "Channel_Normal_Table",
    "global": "Global_Table",
    "statistic": "Channel_Statistic_Table",
}


class ArbinLoader(Loader):
    """ Class for loading arbin-data from res-files."""

    # Note: the class is sub-classing Loader. At the moment, Loader does not really contain anything...

    def __init__(self):
        """initiates the ArbinLoader class"""
        # could use __init__(self, cellpydata_object) and set self.logger = cellpydata_object.logger etc.
        # then remember to include that as prm in "out of class" functions
        # self.prms = prms
        self.logger = logging.getLogger(__name__)
        # use the following prm to limit to loading only one cycle or from cycle>x to cycle<x+n
        # prms.Reader["limit_loaded_cycles"] = [cycle from, cycle to]

        self.headers_normal = get_headers_normal()
        self.headers_global = self.get_headers_global()
        self.current_chunk = 0  # use this to set chunks to load

    @staticmethod
    def get_raw_units():
        """Include the settings for the units used by the instrument.

        The units are defined w.r.t. the SI units ('unit-fractions'; currently only units that are multiples of
        Si units can be used). For example, for current defined in mA, the value for the
        current unit-fraction will be 0.001.

        Returns: dictionary containing the unit-fractions for current, charge, and mass

        """
        raw_units = dict()
        raw_units["current"] = 1.0  # A
        raw_units["charge"] = 1.0  # Ah
        raw_units["mass"] = 0.001  # g
        return raw_units

    @staticmethod
    def get_headers_global():
        """Defines the so-called global column headings for Arbin .res-files"""
        headers = dict()
        # - global column headings (specific for Arbin)
        headers["applications_path_txt"] = 'Applications_Path'
        headers["channel_index_txt"] = 'Channel_Index'
        headers["channel_number_txt"] = 'Channel_Number'
        headers["channel_type_txt"] = 'Channel_Type'
        headers["comments_txt"] = 'Comments'
        headers["creator_txt"] = 'Creator'
        headers["daq_index_txt"] = 'DAQ_Index'
        headers["item_id_txt"] = 'Item_ID'
        headers["log_aux_data_flag_txt"] = 'Log_Aux_Data_Flag'
        headers["log_chanstat_data_flag_txt"] = 'Log_ChanStat_Data_Flag'
        headers["log_event_data_flag_txt"] = 'Log_Event_Data_Flag'
        headers["log_smart_battery_data_flag_txt"] = 'Log_Smart_Battery_Data_Flag'
        headers["mapped_aux_conc_cnumber_txt"] = 'Mapped_Aux_Conc_CNumber'
        headers["mapped_aux_di_cnumber_txt"] = 'Mapped_Aux_DI_CNumber'
        headers["mapped_aux_do_cnumber_txt"] = 'Mapped_Aux_DO_CNumber'
        headers["mapped_aux_flow_rate_cnumber_txt"] = 'Mapped_Aux_Flow_Rate_CNumber'
        headers["mapped_aux_ph_number_txt"] = 'Mapped_Aux_PH_Number'
        headers["mapped_aux_pressure_number_txt"] = 'Mapped_Aux_Pressure_Number'
        headers["mapped_aux_temperature_number_txt"] = 'Mapped_Aux_Temperature_Number'
        headers["mapped_aux_voltage_number_txt"] = 'Mapped_Aux_Voltage_Number'
        headers["schedule_file_name_txt"] = 'Schedule_File_Name'  # KEEP FOR CELLPY FILE FORMAT
        headers["start_datetime_txt"] = 'Start_DateTime'
        headers["test_id_txt"] = 'Test_ID'  # KEEP FOR CELLPY FILE FORMAT
        headers["test_name_txt"] = 'Test_Name'  # KEEP FOR CELLPY FILE FORMAT
        return headers

    @staticmethod
    def get_raw_limits():
        """Include the settings for how to decide what kind of step you are examining here.

        The raw limits are 'epsilons' used to check if the current and/or voltage is stable (for example
        for galvanostatic steps, one would expect that the current is stable (constant) and non-zero).
        It is expected that different instruments (with different resolution etc.) have different
        'epsilons'.

        Returns: the raw limits (dict)

        """
        raw_limits = dict()
        raw_limits["current_hard"] = 0.0000000000001
        raw_limits["current_soft"] = 0.00001
        raw_limits["stable_current_hard"] = 2.0
        raw_limits["stable_current_soft"] = 4.0
        raw_limits["stable_voltage_hard"] = 2.0
        raw_limits["stable_voltage_soft"] = 4.0
        raw_limits["stable_charge_hard"] = 0.9
        raw_limits["stable_charge_soft"] = 5.0
        raw_limits["ir_change"] = 0.00001
        return raw_limits

    def __get_res_connector(self, temp_filename):

        if use_ado:
            is64bit_python = check64bit(current_system="python")
            if is64bit_python:
                constr = 'Provider=Microsoft.ACE.OLEDB.12.0; Data Source=%s' % temp_filename
            else:
                constr = 'Provider=Microsoft.Jet.OLEDB.4.0; Data Source=%s' % temp_filename
            return constr

        if SEARCH_FOR_ODBC_DRIVERS:
            try:
                driver = [driver for driver in dbloader.drivers() if 'Microsoft Access Driver' in driver][0]
            except IndexError as e:
                driver = DRIVER
                if is_macos:
                    driver = "/usr/local/lib/libmdbodbc.dylib"
                else:
                    self.logger.error(e)
                    print(e)
                    print("Could not find any odbc-drivers."
                          "Check out the homepage of pydobc for info on installing drivers")

            self.logger.debug("odbc constr: {}".format(driver))

        else:
            is64bit_python = check64bit(current_system="python")
            if is64bit_python:
                driver = '{Microsoft Access Driver (*.mdb, *.accdb)}'
            else:
                driver = 'Microsoft Access Driver (*.mdb)'
            self.logger.debug("odbc constr: {}".format(driver))
        constr = 'Driver=%s;Dbq=%s' % (driver, temp_filename)
        return constr

    def _clean_up_loadres(self, cur, conn, filename):
        if cur is not None:
            cur.close()  # adodbapi
        if conn is not None:
            conn.close()  # adodbapi
        if os.path.isfile(filename):
            try:
                os.remove(filename)
            except WindowsError as e:
                self.logger.warning("could not remove tmp-file\n%s %s" % (filename, e))

    def load(self, file_name):
        """Load a raw data-file

        Args:
            file_name (path)

        Returns:
            loaded test
        """

        raw_file_loader = self.loader
        new_rundata = raw_file_loader(file_name)
        new_rundata = self.inspect(new_rundata)
        return new_rundata

    def inspect(self, run_data):
        """inspect the file.

        -adds missing columns (with np.nan)
        """

        checked_rundata = []
        for data in run_data:
            new_cols = data.dfdata.columns
            for col in self.headers_normal:
                if col not in new_cols:
                    data.dfdata[col] = np.nan
            checked_rundata.append(data)
        return checked_rundata

    def _iterdump(self, file_name, headers=None):
        """
        Function for dumping values from a file.

        Should only be used by developers.

        Args:
            file_name: name of the file
            headers: list of headers to pick
                default:
                ["Discharge_Capacity", "Charge_Capacity"]

        Returns: pandas.DataFrame

        """
        if headers is None:
            headers = ["Discharge_Capacity", "Charge_Capacity"]

        step_txt = self.headers_normal['step_index_txt']
        point_txt = self.headers_normal['data_point_txt']
        cycle_txt = self.headers_normal['cycle_index_txt']

        self.logger.debug("iterating through file: %s" % file_name)
        if not os.path.isfile(file_name):
            print("Missing file_\n   %s" % file_name)

        filesize = os.path.getsize(file_name)
        hfilesize = humanize_bytes(filesize)
        txt = "Filesize: %i (%s)" % (filesize, hfilesize)
        self.logger.info(txt)

        table_name_global = TABLE_NAMES["global"]
        table_name_stats = TABLE_NAMES["statistic"]
        table_name_normal = TABLE_NAMES["normal"]

        # creating temporary file and connection

        temp_dir = tempfile.gettempdir()
        temp_filename = os.path.join(temp_dir, os.path.basename(file_name))
        shutil.copy2(file_name, temp_dir)
        constr = self.__get_res_connector(temp_filename)

        if use_ado:
            conn = dbloader.connect(constr)
        else:
            conn = dbloader.connect(constr, autocommit=True)

        self.logger.debug("tmp file: %s" % temp_filename)
        self.logger.debug("constr str: %s" % constr)

        # --------- read global-data ------------------------------------
        self.logger.debug("reading global data table")
        sql = "select * from %s" % table_name_global
        global_data_df = pd.read_sql_query(sql, conn)
        # col_names = list(global_data_df.columns.values)
        self.logger.debug("sql statement: %s" % sql)

        tests = global_data_df[self.headers_normal['test_id_txt']]
        number_of_sets = len(tests)
        self.logger.debug("number of datasets: %i" % number_of_sets)
        self.logger.debug("only selecting first test")
        test_no = 0
        self.logger.debug("setting data for test number %i" % test_no)
        loaded_from = file_name
        # fid = FileID(file_name)
        start_datetime = global_data_df[self.headers_global['start_datetime_txt']][test_no]
        test_ID = int(global_data_df[self.headers_normal['test_id_txt']][test_no])  # OBS
        test_name = global_data_df[self.headers_global['test_name_txt']][test_no]

        # --------- read raw-data (normal-data) -------------------------
        self.logger.debug("reading raw-data")

        columns = ["Data_Point", "Step_Index", "Cycle_Index"]
        columns.extend(headers)
        columns_txt = ", ".join(["%s"] * len(columns)) % tuple(columns)

        sql_1 = "select %s " % columns_txt
        sql_2 = "from %s " % table_name_normal
        sql_3 = "where %s=%s " % (self.headers_normal['test_id_txt'], test_ID)
        sql_5 = "order by %s" % self.headers_normal['data_point_txt']
        import time
        info_list = []
        info_header = ["cycle", "row_count", "start_point", "end_point"]
        info_header.extend(headers)
        self.logger.info(" ".join(info_header))
        self.logger.info("-------------------------------------------------")

        for cycle_number in range(1, 2000):
            t1 = time.time()
            self.logger.debug("picking cycle %i" % cycle_number)
            sql_4 = "AND %s=%i " % (cycle_txt, cycle_number)
            sql = sql_1 + sql_2 + sql_3 + sql_4 + sql_5
            self.logger.debug("sql statement: %s" % sql)
            normal_df = pd.read_sql_query(sql, conn)
            t2 = time.time()
            dt = t2 - t1
            self.logger.debug("time: %f" % dt)
            if normal_df.empty:
                self.logger.debug("reached the end")
                break
            row_count, _ = normal_df.shape
            start_point = normal_df[point_txt].min()
            end_point = normal_df[point_txt].max()
            last = normal_df.iloc[-1, :]

            step_list = [cycle_number, row_count, start_point, end_point]
            step_list.extend([last[x] for x in headers])
            info_list.append(step_list)

        self._clean_up_loadres(None, conn, temp_filename)
        info_dict = pd.DataFrame(info_list, columns=info_header)
        return info_dict

    def investigate(self, file_name):
        """Investigate a .res file.

        Args:
            file_name: name of the file

        Returns: dictionary with div. stats and info.

        """
        step_txt = self.headers_normal['step_index_txt']
        point_txt = self.headers_normal['data_point_txt']
        cycle_txt = self.headers_normal['cycle_index_txt']

        self.logger.debug("investigating file: %s" % file_name)
        if not os.path.isfile(file_name):
            print("Missing file_\n   %s" % file_name)

        filesize = os.path.getsize(file_name)
        hfilesize = humanize_bytes(filesize)
        txt = "Filesize: %i (%s)" % (filesize, hfilesize)
        self.logger.info(txt)

        table_name_global = TABLE_NAMES["global"]
        table_name_stats = TABLE_NAMES["statistic"]
        table_name_normal = TABLE_NAMES["normal"]

        # creating temporary file and connection

        temp_dir = tempfile.gettempdir()
        temp_filename = os.path.join(temp_dir, os.path.basename(file_name))
        shutil.copy2(file_name, temp_dir)
        constr = self.__get_res_connector(temp_filename)

        if use_ado:
            conn = dbloader.connect(constr)
        else:
            conn = dbloader.connect(constr, autocommit=True)

        self.logger.debug("tmp file: %s" % temp_filename)
        self.logger.debug("constr str: %s" % constr)

        # --------- read global-data ------------------------------------
        self.logger.debug("reading global data table")
        sql = "select * from %s" % table_name_global
        global_data_df = pd.read_sql_query(sql, conn)
        # col_names = list(global_data_df.columns.values)
        self.logger.debug("sql statement: %s" % sql)

        tests = global_data_df[self.headers_normal['test_id_txt']]
        number_of_sets = len(tests)
        self.logger.debug("number of datasets: %i" % number_of_sets)
        self.logger.debug("only selecting first test")
        test_no = 0
        self.logger.debug("setting data for test number %i" % test_no)
        loaded_from = file_name
        # fid = FileID(file_name)
        start_datetime = global_data_df[self.headers_global['start_datetime_txt']][test_no]
        test_ID = int(global_data_df[self.headers_normal['test_id_txt']][test_no])  # OBS
        test_name = global_data_df[self.headers_global['test_name_txt']][test_no]

        # --------- read raw-data (normal-data) -------------------------
        self.logger.debug("reading raw-data")

        columns = ["Data_Point", "Step_Index", "Cycle_Index"]
        columns_txt = ", ".join(["%s"] * len(columns)) % tuple(columns)

        sql_1 = "select %s " % columns_txt
        sql_2 = "from %s " % table_name_normal
        sql_3 = "where %s=%s " % (self.headers_normal['test_id_txt'], test_ID)
        sql_5 = "order by %s" % self.headers_normal['data_point_txt']
        import time
        info_list = []
        info_header = ["cycle", "step", "row_count", "start_point", "end_point"]
        self.logger.info(" ".join(info_header))
        self.logger.info("-------------------------------------------------")
        for cycle_number in range(1, 2000):
            t1 = time.time()
            self.logger.debug("picking cycle %i" % cycle_number)
            sql_4 = "AND %s=%i " % (cycle_txt, cycle_number)
            sql = sql_1 + sql_2 + sql_3 + sql_4 + sql_5
            self.logger.debug("sql statement: %s" % sql)
            normal_df = pd.read_sql_query(sql, conn)
            t2 = time.time()
            dt = t2 - t1
            self.logger.debug("time: %f" % dt)
            if normal_df.empty:
                self.logger.debug("reached the end")
                break
            row_count, _ = normal_df.shape
            steps = normal_df[self.headers_normal['step_index_txt']].unique()
            txt = "cycle %i: %i [" % (cycle_number, row_count)
            for step in steps:
                self.logger.debug(" step: %i" % step)
                step_df = normal_df.loc[normal_df[step_txt] == step]
                step_row_count, _ = step_df.shape
                start_point = step_df[point_txt].min()
                end_point = step_df[point_txt].max()
                txt += " %i-(%i)" % (step, step_row_count)
                step_list = [cycle_number, step, step_row_count, start_point, end_point]
                info_list.append(step_list)

            txt += "]"
            self.logger.info(txt)

        self._clean_up_loadres(None, conn, temp_filename)
        info_dict = pd.DataFrame(info_list, columns=info_header)
        return info_dict

    def repair(self, file_name):
        """try to repair a broken/corrupted file"""
        raise NotImplemented

    def dump(self, file_name, path):
        """Dumps the raw file to an intermediate hdf5 file.

        This method can be used if the raw file is too difficult to load and it
        is likely that it is more efficient to convert it to an hdf5 format
        and then load it using the `from_intermediate_file` function.

        Args:
            file_name: name of the raw file
            path: path to where to store the intermediate hdf5 file (optional)

        Returns:
            full path to stored intermediate hdf5 file
            information about the raw file (needed by the `from_intermediate_file` function)

        """

        # information = None # contains information needed by the from_intermediate_file reader
        # full_path = None
        # return full_path, information
        raise NotImplemented

    def loader(self, file_name, bad_steps=None, **kwargs):
        """Loads data from arbin .res files.

        Args:
            file_name (str): path to .res file.
            bad_steps (list of tuples): (c, s) tuples of steps s (in cycle c) to skip loading.

        Returns:
            new_tests (list of data objects)
        """
        # TODO: insert kwargs - current chunk, only normal data, etc

        new_tests = []
        if not os.path.isfile(file_name):
            self.logger.info("Missing file_\n   %s" % file_name)
            return None

        self.logger.debug("in loader")
        self.logger.debug("filename: %s" % file_name)

        filesize = os.path.getsize(file_name)
        hfilesize = humanize_bytes(filesize)
        txt = "Filesize: %i (%s)" % (filesize, hfilesize)
        self.logger.debug(txt)
        if filesize > prms.Instruments["max_res_filesize"] and not prms.Reader["load_only_summary"]:
            error_message = "\nERROR (loader):\n"
            error_message += "%s > %s - File is too big!\n" % (
                hfilesize, humanize_bytes(prms.Instruments["max_res_filesize"]))
            error_message += "(edit prms.Instruments['max_res_filesize'])\n"
            print(error_message)
            return None

        table_name_global = TABLE_NAMES["global"]
        table_name_stats = TABLE_NAMES["statistic"]
        table_name_normal = TABLE_NAMES["normal"]

        # creating temporary file and connection

        temp_dir = tempfile.gettempdir()
        temp_filename = os.path.join(temp_dir, os.path.basename(file_name))
        shutil.copy2(file_name, temp_dir)
        self.logger.debug("tmp file: %s" % temp_filename)

        use_mdbtools = False
        if use_subprocess:
            use_mdbtools = True
        if is_posix:
            use_mdbtools = True

        # windows with same python bit as windows bit (the ideal case)
        if not use_mdbtools:
            constr = self.__get_res_connector(temp_filename)

            if use_ado:
                conn = dbloader.connect(constr)
            else:
                conn = dbloader.connect(constr, autocommit=True)
            self.logger.debug("constr str: %s" % constr)

            self.logger.debug("reading global data table")
            sql = "select * from %s" % table_name_global
            self.logger.debug("sql statement: %s" % sql)
            global_data_df = pd.read_sql_query(sql, conn)
            # col_names = list(global_data_df.columns.values)

        else:
            import subprocess
            if is_posix:
                if is_macos:
                    self.logger.debug("\nMAC OSX USING MDBTOOLS")
                else:
                    self.logger.debug("\nPOSIX USING MDBTOOLS")
            else:
                self.logger.debug("\nWINDOWS USING MDBTOOLS-WIN")

            # creating tmp-filenames
            temp_csv_filename_global = os.path.join(temp_dir, "global_tmp.csv")
            temp_csv_filename_normal = os.path.join(temp_dir, "normal_tmp.csv")
            temp_csv_filename_stats = os.path.join(temp_dir, "stats_tmp.csv")

            # making the cmds
            mdb_prms = [(table_name_global, temp_csv_filename_global), (table_name_normal, temp_csv_filename_normal),
                        (table_name_stats, temp_csv_filename_stats)]

            # executing cmds
            for table_name, tmp_file in mdb_prms:
                with open(tmp_file, "w") as f:
                    subprocess.call([sub_process_path, temp_filename, table_name], stdout=f)
                    self.logger.debug(f"ran mdb-export {str(f)} {table_name}")

            # use pandas to load in the data
            global_data_df = pd.read_csv(temp_csv_filename_global)

        tests = global_data_df[self.headers_normal['test_id_txt']]  # OBS
        number_of_sets = len(tests)
        self.logger.debug("number of datasets: %i" % number_of_sets)

        for test_no in range(number_of_sets):
            data = DataSet()
            data.test_no = test_no
            data.loaded_from = file_name
            fid = FileID(file_name)
            # data.parent_filename = os.path.basename(file_name)# name of the .res file it is loaded from
            data.channel_index = int(global_data_df[self.headers_global['channel_index_txt']][test_no])
            data.channel_number = int(global_data_df[self.headers_global['channel_number_txt']][test_no])
            data.creator = global_data_df[self.headers_global['creator_txt']][test_no]
            data.item_ID = global_data_df[self.headers_global['item_id_txt']][test_no]
            data.schedule_file_name = global_data_df[self.headers_global['schedule_file_name_txt']][test_no]
            data.start_datetime = global_data_df[self.headers_global['start_datetime_txt']][test_no]
            data.test_ID = int(global_data_df[self.headers_normal['test_id_txt']][test_no])  # OBS
            data.test_name = global_data_df[self.headers_global['test_name_txt']][test_no]
            data.raw_data_files.append(fid)

            self.logger.debug("reading raw-data")
            if not use_mdbtools:
                # --------- read raw-data (normal-data) -------------------------
                length_of_test, normal_df = self._load_res_normal_table(conn, data.test_ID, bad_steps)
                # --------- read stats-data (summary-data) ----------------------
                sql = "select * from %s where %s=%s order by %s" % (table_name_stats,
                                                                    self.headers_normal['test_id_txt'],
                                                                    data.test_ID,
                                                                    self.headers_normal['data_point_txt'])
                summary_df = pd.read_sql_query(sql, conn)
                self._clean_up_loadres(None, conn, temp_filename)
            else:
                normal_df = pd.read_csv(temp_csv_filename_normal)
                # filter on test ID
                normal_df = normal_df[normal_df[self.headers_normal['test_id_txt']] == data.test_ID]
                length_of_test = normal_df.shape[0]
                summary_df = pd.read_csv(temp_csv_filename_stats)
                # clean up
                for f in [temp_filename, temp_csv_filename_stats, temp_csv_filename_normal,
                          temp_csv_filename_global]:
                    if os.path.isfile(f):
                        try:
                            os.remove(f)
                        except WindowsError as e:
                            self.logger.warning(
                                f"could not remove tmp-file\n{f} {e}"
                            )

            if summary_df.empty and prms.Reader.use_cellpy_stat_file:
                txt = "\nCould not find any summary (stats-file)!"
                txt += "\n -> issue make_summary(use_cellpy_stat_file=False)"
                warnings.warn(txt)
            # normal_df = normal_df.set_index("Data_Point")
            data.dfsummary = summary_df
            data.dfdata = normal_df
            data.raw_data_files_length.append(length_of_test)
            new_tests.append(data)
        return new_tests

    def _normal_table_generator(self, **kwargs):
        pass

    # noinspection PyPep8Naming
    def _load_res_normal_table(self, conn, test_ID, bad_steps):
        # Note that this function is run each time you use the loader. This means that it is not ideal for
        # handling generators etc

        self.logger.debug("starting loading raw-data")
        table_name_normal = TABLE_NAMES["normal"]

        if prms.Reader["load_only_summary"]:  # SETTING
            warnings.warn("not implemented")

        if prms.Reader["select_minimal"]:  # SETTING
            columns = MINIMUM_SELECTION
            columns_txt = ", ".join(["%s"] * len(columns)) % tuple(columns)
        else:
            columns_txt = "*"

        sql_1 = "select %s " % columns_txt
        sql_2 = "from %s " % table_name_normal
        sql_3 = "where %s=%s " % (self.headers_normal['test_id_txt'], test_ID)
        sql_4 = ""

        if bad_steps is not None:
            if not isinstance(bad_steps, (list, tuple)):
                bad_steps = [bad_steps, ]
            for bad_cycle, bad_step in bad_steps:
                self.logger.debug("bad_step def: [c=%i, s=%i]" % (bad_cycle, bad_step))
                sql_4 += "AND NOT (%s=%i " % (self.headers_normal['cycle_index_txt'], bad_cycle)
                sql_4 += "AND %s=%i) " % (self.headers_normal['step_index_txt'], bad_step)

        if prms.Reader["limit_loaded_cycles"]:
            if len(prms.Reader["limit_loaded_cycles"]) > 1:
                sql_4 += "AND %s>%i " % (self.headers_normal['cycle_index_txt'], prms.Reader["limit_loaded_cycles"][0])
                sql_4 += "AND %s<%i " % (self.headers_normal['cycle_index_txt'], prms.Reader["limit_loaded_cycles"][-1])
            else:
                sql_4 = "AND %s=%i " % (self.headers_normal['cycle_index_txt'], prms.Reader["limit_loaded_cycles"][0])

        sql_5 = "order by %s" % self.headers_normal['data_point_txt']
        sql = sql_1 + sql_2 + sql_3 + sql_4 + sql_5

        self.logger.debug("INFO ABOUT LOAD RES NORMAL")
        self.logger.debug("sql statement: %s" % sql)

        if not prms.Instruments['chunk_size']:
            self.logger.debug("no chunk-size given")
            normal_df = pd.read_sql_query(sql, conn)
            length_of_test = normal_df.shape[0]
            self.logger.debug("loaded to normal_df (length = %i)" % length_of_test)
        else:
            self.logger.debug("chunk-size: %s" % int(prms.Instruments['chunk_size']))
            self.logger.debug("creating a pd.read_sql_query generator")
            normal_df_reader = pd.read_sql_query(sql, conn, chunksize=prms.Instruments['chunk_size'])
            normal_df = None
            chunk_number = 0
            self.logger.debug("created pandas sql reader")
            self.logger.debug("iterating chunk-wise")
            for i, chunk in enumerate(normal_df_reader):
                self.logger.debug("iteration number %i" % i)
                if prms.Instruments["max_chunks"]:
                    self.logger.debug("max number of chunks mode (%i)" % prms.Instruments["max_chunks"])
                    if chunk_number < prms.Instruments["max_chunks"]:
                        normal_df = pd.concat([normal_df, chunk], ignore_index=True)
                        self.logger.debug("chunk %i of %i" % (i, prms.Instruments["max_chunks"]))
                    else:
                        break
                else:
                    try:
                        normal_df = pd.concat([normal_df, chunk], ignore_index=True)
                        self.logger.debug("concatenated new chunk")
                    except MemoryError:
                        self.logger.error(" - Could not read complete file (MemoryError).")
                        self.logger.error("Last successfully loaded chunk number:", chunk_number)
                        self.logger.error("Chunk size:", prms.Instruments['chunk_size'])
                        break
                chunk_number += 1
            length_of_test = normal_df.shape[0]
            self.logger.debug("finished iterating (#rows: %i)", length_of_test)
        return length_of_test, normal_df


if __name__ == '__main__':
    import logging
    from cellpy import log

    log.setup_logging(default_level=logging.DEBUG)
