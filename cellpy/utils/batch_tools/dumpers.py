import logging
import os

from cellpy import prms
from cellpy.utils.batch_tools.batch_helpers import generate_folder_names


def csv_dumper(**kwargs):
    """dump data to csv"""
    logging.info("dumping to csv")
    barn = kwargs["barn"]
    farms = kwargs["farms"]
    experiments = kwargs["experiments"]
    for experiment, farm in zip(experiments, farms):
        name = experiment.journal.name
        project = experiment.journal.project
        project_dir, batch_dir, raw_dir = \
            experiment.journal.paginate()
        if batch_dir is None:
            logging.info("have to generate folder-name on the fly")
            out_data_dir, project_dir, batch_dir, raw_dir = \
                generate_folder_names(name, project)

        if barn == "batch_dir":
            out_dir = batch_dir
        elif barn == "project_dir":
            out_dir = project_dir
        elif barn == "raw_dir":
            out_dir = raw_dir
        else:
            out_dir = barn

        for animal in farm:
            file_name = os.path.join(
                out_dir, "summary_%s_%s.csv" % (
                    animal.name,
                    name
                )
            )
            logging.info(f"> {file_name}")
            animal.to_csv(file_name, sep=prms.Reader.sep)


def excel_dumper(**kwargs):
    """dump data to excel xlxs-format"""
    pass


def origin_dumper(**kwargs):
    """dump data to a format suitable for use in OriginLab"""
    pass


def screen_dumper(**kwargs):
    """dump data to screen"""
    farms = kwargs["farms"]
    logging.info("dumping to screen")

    print("\n[Screen dumper]")
    try:
        if len(farms) == 1:
            print(f"You have one farm with little pandas.")

        else:
            print(f"You have {len(farms)} farms with little pandas.")
    except TypeError:
        print(" - your farm has burned to the ground.")
    else:
        for number, farm in enumerate(farms):
            print(f"[#{number+1}]You have {len(farm)} "
                  f"little pandas in this farm.")
            for animal in farm:
                print(80*"=")
                try:
                    print(animal.name)
                except AttributeError:
                    print("no-name")
                print(80*"-")
                print(animal.head(5))
                print()
