import attr
from attr.validators import instance_of, optional
import numpy as np
import pandas as pd
import pathlib
import matplotlib.pyplot as plt
import tifffile
import os
import itertools
import xarray as xr
from collections import namedtuple
from datetime import datetime
import colorama

colorama.init()
import copy
import warnings

from calcium_bflow_analysis.calcium_over_time import FileFinder
from calcium_bflow_analysis.analog_trace import (
    AnalogAcquisitionType,
    analog_trace_runner,
)
from calcium_bflow_analysis.dff_analysis_and_plotting.dff_analysis import (
    calc_dff,
    calc_dff_batch,
    scatter_spikes,
    plot_mean_vals,
)
from calcium_bflow_analysis.dff_analysis_and_plotting.plot_cells_and_traces import (
    display_heatmap,
)


@attr.s(slots=True)
class VascOccParser:
    """
    A class that provides the analysis pipeline for stacks with vascular occluder. Meaning,
    Data acquired in a before-during-after scheme, where "during" is the perturbation done
    to the system, occlusion of an artery in this case. The class needs to know how many frames
    were acquired before the perturbation and how many were acquired during. It also needs
    other metadata, such as the framerate, and the IDs of cells that the CaImAn pipeline
    accidentally labeled as active components. If the data contains analog recordings as well,
    of the mouse's movements and air puffs, they will be integrated into the analysis as well.
    If one of the data channels contains co-labeling with a different, usually morphological,
    fluorophore indicating the cell type, it will be integrated as well.
    """

    data_files = attr.ib(validator=instance_of(pd.DataFrame))
    frames_before_stim = attr.ib(default=1000)
    len_of_epoch_in_frames = attr.ib(default=1000)
    analog = attr.ib(
        default=AnalogAcquisitionType.NONE, validator=instance_of(AnalogAcquisitionType)
    )
    with_colabeling = attr.ib(default=False, validator=instance_of(bool))
    serialize = attr.ib(default=True, validator=optional(instance_of(str)))
    fps = attr.ib(init=False)
    dff = attr.ib(init=False)
    colabel_idx = attr.ib(init=False)
    frames_after_stim = attr.ib(init=False)
    start_time = attr.ib(init=False)
    timestamps = attr.ib(init=False)
    num_of_channels = attr.ib(init=False)
    sliced_fluo = attr.ib(init=False)
    OccMetadata = attr.ib(init=False)

    def __attrs_post_init__(self):
        self.OccMetadata = namedtuple("OccMetadata", ["before", "during", "after"])

    def run(self):
        if self.analog is not AnalogAcquisitionType.NONE:
            self.__run_with_analog()  # colabeling check is inside there
            self.dff = self.sliced_fluo.loc["all"].values
        elif self.with_colabeling:
            self.dff = self._load_dff()
            colabel_idx = self._load_colabeled_idx()
        else:
            self.dff = calc_dff_batch(self.data_files["caiman"])

    def __run_with_analog(self):
        """ Helper function to run sequentially all needed analysis of dF/F + Analog data """
        list_of_sliced_fluo = (
            []
        )  # we have to compare each file with its analog data, individually
        for idx, row in self.data_files.iterrows():
            self._get_params(row["tif"])
            dff = calc_dff((row["caiman"]))
            analog_data = pd.read_csv(
                row["analog"], header=None, names=["stimulus", "run"], index_col=False
            )
            occ_metadata = self.OccMetadata(
                self.frames_before_stim,
                self.len_of_epoch_in_frames,
                self.frames_after_stim,
            )
            analog_trace = analog_trace_runner(
                row["tif"],
                analog_data,
                self.analog,
                self.fps,
                self.start_time,
                self.timestamps,
                occluder=True,
                occ_metadata=occ_metadata,
            )
            analog_trace.run()
            # multiplying the trace by the dff changes analog_trace. To overcome
            # this weird issue we're copying it.
            copied_trace = copy.deepcopy(analog_trace)  # for some reason,
            list_of_sliced_fluo.append(copied_trace * dff)  # overloaded __mul__
        if self.with_colabeling:
            self.colabel_idx = self._load_colabeled_idx()
        print("Concatenating FOVs into a single data structure...")
        self.sliced_fluo: xr.DataArray = concat_vasc_occ_dataarrays(list_of_sliced_fluo)
        if self.serialize is not False:
            print("Writing to disk...")
            self._serialize_results(row["tif"].parent)

    def _serialize_results(self, foldername: pathlib.Path):
        """ Write to disk the generated concatenated DataArray """
        fname = "vasc_occ_parsed.nc"
        if self.serialize is not None:
            fname = self.serialize
        self.sliced_fluo.attrs["fps"] = self.fps
        self.sliced_fluo.attrs["frames_before_occ"] = self.frames_before_stim
        self.sliced_fluo.attrs["frames_during_occ"] = self.len_of_epoch_in_frames
        self.sliced_fluo.attrs["frames_after_occ"] = self.frames_after_stim
        if self.with_colabeling:
            self.sliced_fluo.attrs["colabeled"] = self.colabel_idx
        self.sliced_fluo.to_netcdf(
            str(foldername / (fname + ".nc")), mode="w"
        )  # TODO: compress

    def _get_params(self, fname: pathlib.Path):
        """ Get general stack parameters from the TiffFile object """
        try:
            print("Getting TIF parameters...")
            with tifffile.TiffFile(str(fname)) as f:
                si_meta = f.scanimage_metadata
                self.fps = si_meta["FrameData"]["SI.hRoiManager.scanFrameRate"]
                self.num_of_channels = len(
                    si_meta["FrameData"]["SI.hChannels.channelsActive"]
                )
                num_of_frames = si_meta["FrameData"]["SI.hStackManager.framesPerSlice"]
                self.frames_after_stim = num_of_frames - (
                    self.frames_before_stim + self.len_of_epoch_in_frames
                )
                self.start_time = str(
                    datetime.fromtimestamp(os.path.getmtime(self.data_files["tif"][0]))
                )
                self.timestamps = np.arange(num_of_frames) / self.fps
                print("Done without errors!")
        except TypeError:
            warnings.warn("Failed to parse ScanImage metadata")
            self.start_time = None
            self.timestamps = None
            self.frames_after_stim = 1000
            self.fps = 58.31

    def _load_colabeled_idx(self):
        """ Loads the indices of the colabeled cells from all found files """
        colabel_idx = []
        num_of_cells = 0
        for _, row in self.data_files.iterrows():
            cur_data = np.load(row.caiman)["F_dff"]
            cur_idx = np.load(row.colabeled)
            assert cur_idx.max() <= cur_data.shape[0]
            cur_idx += num_of_cells
            colabel_idx.append(cur_idx)
            num_of_cells += cur_data.shape[0]

        colabel_idx = np.array(list(itertools.chain.from_iterable(colabel_idx)))
        return colabel_idx

    def _load_dff(self):
        """ Loads the dF/F data from all found files """
        dff = []
        for _, row in self.data_files.iterrows():
            cur_data = np.load(row.caiman)["F_dff"]
            dff.append(cur_data)
        dff = np.concatenate(dff)
        return dff

    def _display_analog_traces(self, ax_puff, ax_jux, ax_run, data):
        """ Show three Axes of the analog data """
        ax_puff.plot(data.stim_vec)
        ax_puff.invert_yaxis()
        ax_puff.set_ylabel("Direct air puff")
        ax_puff.set_xlabel("")
        ax_puff.set_xticks([])
        ax_jux.plot(data.juxta_vec)
        ax_jux.invert_yaxis()
        ax_jux.set_ylabel("Juxtaposed puff")
        ax_jux.set_xlabel("")
        ax_jux.set_xticks([])
        ax_run.plot(data.run_vec)
        ax_run.invert_yaxis()
        ax_run.set_ylabel("Run times")
        ax_run.set_xlabel("")
        ax_run.set_xticks([])

    def _display_occluder(self, ax, data_length):
        """ Show the occluder timings """
        occluder = np.zeros((data_length))
        occluder[
            self.frames_before_stim : self.frames_before_stim
            + self.len_of_epoch_in_frames
        ] = 1
        time = np.arange(data_length) / self.fps
        ax.plot(time, occluder)
        ax.get_xaxis().set_ticks_position("top")

        ax.invert_yaxis()

        ax.set_ylabel("Artery occlusion")
        ax.set_xlabel("")


def concat_vasc_occ_dataarrays(da_list: list):
    """ Take a list of DataArrays and concatenate them together
    while keeping the index integrity """
    new_da_list = []
    num_of_neurons = 0
    crd_time = da_list[0].time.values
    crd_epoch = da_list[0].epoch.values
    for idx, da in enumerate(da_list):
        crd_neuron = np.arange(num_of_neurons, num_of_neurons + len(da.neuron))
        if len(da.time) != len(crd_time):
            crd_time = da.time.values
        reindexed_da = xr.DataArray(
            data=da.data,
            dims=["epoch", "neuron", "time"],
            coords={"epoch": crd_epoch, "neuron": crd_neuron, "time": crd_time},
            attrs=da.attrs,
        )
        new_da_list.append(reindexed_da)
        num_of_neurons += len(da.neuron)

    return xr.concat(new_da_list, dim="neuron")


if __name__ == "__main__":
    folder = pathlib.Path("/data/David/Vascular_occ_Laducq_2P/#5_right_hemi_occluder")
    glob = r"fov*_L_CON*.tif"
    assert folder.exists()
    folder_globs = {folder: glob}
    analog = AnalogAcquisitionType.TREADMILL
    with_colabeling = False
    filefinder = FileFinder(
        results_folder=folder,
        folder_globs=folder_globs,
        analog=analog,
        with_colabeled=with_colabeling,
    )
    data_files = filefinder.find_files()
    frames_before_stim = 3600
    len_of_epoch_in_frames = 7200
    display_each_fov = True
    serialize = "vasc_occ_parsed_contra"
    vasc = VascOccParser(
        data_files=data_files,
        frames_before_stim=frames_before_stim,
        len_of_epoch_in_frames=len_of_epoch_in_frames,
        analog=analog,
        with_colabeling=with_colabeling,
        serialize=serialize,
    )
    vasc.run()
    plt.show(block=True)
