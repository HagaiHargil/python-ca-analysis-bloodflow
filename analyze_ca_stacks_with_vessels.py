import matplotlib.pyplot as plt
from roipoly import roipoly
import tifffile
import numpy as np
from scipy.io import loadmat
from matplotlib.gridspec import GridSpec
from typing import List
from collections import namedtuple
from tkinter import ttk
from tkinter import filedialog
from tkinter import *


def main():
    """ Analyze calcium traces and compare them to vessel diameter """

    # Parameters
    colors = [f"C{idx}" for idx in range(10)]  # use default matplotlib colormap

    # Main GUI
    root = Tk()
    root.title("Choose what you'd like to analyze")
    style = ttk.Style()
    style.theme_use("clam")

    ca_analysis = BooleanVar(value=True)
    bloodflow_analysis = BooleanVar(value=True)
    time_per_frame = StringVar(value=f"{1 / 30.03}")  # seconds, 30 Hz imaging
    num_of_rois = StringVar(value="1")

    frame = Frame(root)
    frame.pack()
    check_cells = ttk.Checkbutton(frame, text="Analyze calcium?", variable=ca_analysis)
    check_cells.pack()
    check_bloodflow = ttk.Checkbutton(frame, text="Analyze bloodflow?", variable=bloodflow_analysis)
    check_bloodflow.pack()
    label_rois = ttk.Label(frame, text="Number of ROIs: ")
    label_rois.pack()
    rois_entry = ttk.Entry(frame, textvariable=num_of_rois)
    rois_entry.pack()
    label_time_per_frame = ttk.Label(frame, text="Time per frame: ")
    label_time_per_frame.pack()
    time_per_frame_entry = ttk.Entry(frame, textvariable=time_per_frame)
    time_per_frame_entry.pack()
    root.mainloop()

    if ca_analysis.get():
        root1 = Tk()
        root1.withdraw()
        filename = filedialog.askopenfilename(title="Choose a tiff stack for ROIs", filetypes=[("Tiff stack", "*.tif")])
        img_neuron, time_vec, fluo_trace, rois = draw_rois_and_find_fluo(filename=filename,
                                                                         time_per_frame=float(time_per_frame.get()),
                                                                         num_of_rois=int(num_of_rois.get()),
                                                                         colors=colors)

    if bloodflow_analysis.get():
        root2 = Tk()
        root2.withdraw()
        andy_mat = filedialog.askopenfilename(title="Choose a .mat file for vessel analysis",
                                              filetypes=[("MATLAB files", "*.mat")])
        struct_name = "mv_mpP"
        vessel_lines, diameter_data, img_vessels = import_andy_and_plot(filename=andy_mat,
                                                                        struct_name=struct_name,
                                                                        colors=colors)

        if ca_analysis.get():
            idx_of_closest_vessel = find_closest_vessel(rois=rois, vessels=vessel_lines)

            plot_neuron_with_vessel(rois=rois, vessels=vessel_lines, closest=idx_of_closest_vessel,
                                    img_vessels=img_vessels, fluo_trace=fluo_trace, time_vec=time_vec,
                                    diameter_data=diameter_data, img_neuron=img_neuron)

    plt.show()


def draw_rois_and_find_fluo(filename: str, time_per_frame: float,
                            num_of_rois: int, colors: List):

    print("Reading stack...")
    tif = tifffile.imread(filename)
    print("Reading complete.")
    num_of_slices = tif.shape[0]
    max_time = num_of_slices * time_per_frame  # second
    rois = []
    fluorescent_trace = np.zeros((num_of_rois, num_of_slices))

    # Display the mean image and draw ROIs
    mean_image = np.mean(tif, 0)

    for idx in range(num_of_rois):
        plt.figure()
        plt.imshow(mean_image, cmap='gray')
        rois.append(roipoly(roicolor=colors[idx]))
        plt.show(block=True)

    # Calculate the mean fluo. and draw the cells in a single figure
    fig_cells = plt.figure()
    ax_cells = fig_cells.add_subplot(121)
    ax_cells.set_title("Field of View")
    ax_cells.imshow(mean_image, cmap='gray')

    for idx, roi in enumerate(rois):
        cur_mask = roi.getMask(mean_image)
        fluorescent_trace[idx, :] = np.mean(tif[:, cur_mask], axis=-1)
        roi.displayROI()

    # Add offset to the traces
    maxes = np.max(fluorescent_trace, 1).reshape((num_of_rois, 1))
    maxes = np.tile(maxes, num_of_slices)
    assert maxes.shape == fluorescent_trace.shape

    fluorescent_trace_normed = fluorescent_trace / maxes
    assert np.max(fluorescent_trace_normed) <= 1

    offset_vec = np.arange(num_of_rois).reshape((num_of_rois, 1))
    offset_vec = np.tile(offset_vec, num_of_slices)
    assert offset_vec.shape == fluorescent_trace_normed.shape

    fluorescent_trace_normed_off = fluorescent_trace_normed + offset_vec
    time_vec = np.linspace(start=0, stop=max_time, num=num_of_slices).reshape((1, num_of_slices))
    time_vec = np.tile(time_vec, (num_of_rois, 1))
    assert time_vec.shape == fluorescent_trace_normed_off.shape

    # Plot fluorescence results

    ax = fig_cells.add_subplot(122)
    ax.plot(time_vec.T, fluorescent_trace_normed_off.T)
    ax.set_xlabel("Time [sec]")
    ax.set_ylabel("Cell ID")
    ax.set_yticks(np.arange(num_of_rois) + 0.5)
    ax.set_yticklabels(np.arange(1, num_of_rois + 1))
    ax.set_title("Fluorescence trace")

    return mean_image, time_vec, fluorescent_trace, rois


def import_andy_and_plot(filename: str, struct_name: str, colors: List):
    """
    Import the output of the VesselDiameter.m Matlab script and draw it
    :param filename:
    :param struct_name: inside struct
    :return:
    """
    andy = loadmat(filename)
    img = andy[struct_name][0][0]['first_frame']
    num_of_vessels = andy[struct_name].shape[1]
    fig_vessels = plt.figure()
    fig_vessels.suptitle("Vessel Diameter Over Time")
    gs1 = GridSpec(num_of_vessels, 2)

    ax_ves = plt.subplot(gs1[:, 0])
    ax_ves.imshow(img, cmap='gray')

    ax_dia = []
    diameter_data = []
    vessel_lines = []
    Line = namedtuple('Line', ('x1', 'x2', 'y1', 'y2'))
    for idx in range(num_of_vessels):
        ax_dia.append(plt.subplot(gs1[idx, 1]))  # Axes of GridSpec
        diameter_data.append(andy[struct_name][0, idx]['Vessel']['diameter'][0][0])
        line_x1, line_x2 = andy[struct_name][0, idx]['Vessel']['vessel_line'][0][0][0][0][0][0][0][0][:, 0]
        line_y1, line_y2 = andy[struct_name][0, idx]['Vessel']['vessel_line'][0][0][0][0][0][0][0][0][:, 1]
        vessel_lines.append(Line(x1=line_x1, x2=line_x2, y1=line_y1, y2=line_y2))

    for idx in range(num_of_vessels):
        ax_dia[idx].plot(np.arange(diameter_data[idx].shape[1]), diameter_data[idx].T, color=colors[idx])
        ax_ves.plot([vessel_lines[idx].x1, vessel_lines[idx].x2],
                    [vessel_lines[idx].y1, vessel_lines[idx].y2],
                    color=colors[idx])

    return vessel_lines, diameter_data, img


def find_closest_vessel(rois: List[roipoly], vessels: List) -> np.array:
    """ For a list of ROIs, find the index of the nearest blood vessel """

    com_vessels = np.zeros((len(vessels), 2))
    for idx, vessel in enumerate(vessels):
        com_vessels[idx, :] = np.mean((vessel.x1, vessel.x2)), np.mean((vessel.y1, vessel.y2))

    idx_of_closest_vessel = np.zeros((len(rois)), dtype=int)
    for idx, roi in enumerate(rois):
        com_x, com_y = np.mean(roi.allxpoints), np.mean(roi.allypoints)  # center of ROI
        helper_array = np.tile(np.array([com_x, com_y]).reshape((1, 2)), (com_vessels.shape[0], 1))
        dist = np.sqrt(np.sum((helper_array - com_vessels) ** 2, 1))
        idx_of_closest_vessel[idx] = np.argmin(dist)

    return idx_of_closest_vessel


def plot_neuron_with_vessel(rois: List[roipoly], vessels: List, closest: np.array, img_neuron: np.array,
                            fluo_trace: np.array, time_vec: np.array, diameter_data: List, img_vessels: np.array):
    """ Plot them together """

    # Inits
    fig_comp = plt.figure()
    fig_comp.suptitle("Neurons with its closest vessel")
    gs2 = GridSpec(len(rois) * 2, 2)
    colors = [f"C{idx}" for idx in range(10)]

    # Show image with contours on one side
    ax_img = plt.subplot(gs2[:, 0])
    ax_img.imshow(img_vessels, cmap='gray')
    ax_img.imshow(img_neuron, cmap='cool', alpha=0.5)
    for idx, roi in enumerate(rois):
        roi.displayROI()
        closest_vessel = vessels[closest[idx]]
        ax_img.plot([closest_vessel.x1, closest_vessel.x2],
                    [closest_vessel.y1, closest_vessel.y2],
                    color=colors[idx])

    # Go through rois and plot two traces
    ax_neurons = []
    ax_vessels = []
    for idx in range(len(rois)):
        ax_neurons.append(plt.subplot(gs2[idx * 2, 1]))
        ax_vessels.append(plt.subplot(gs2[idx * 2 + 1, 1]))
        ax_neurons[idx].plot(time_vec[idx, :], fluo_trace[idx, :], color=colors[idx])
        closest_vessel = diameter_data[closest[idx]]
        ax_vessels[idx].plot(np.arange(closest_vessel.shape[1]), closest_vessel.T, color=colors[idx])


if __name__ == '__main__':
    main()