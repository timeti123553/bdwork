from pymatgen.electronic_structure.core import Spin, Orbital
from pymatgen.io.vasp.outputs import BSVasprun, Eigenval
from pymatgen.io.vasp.inputs import Kpoints, Poscar, Incar
from pymatgen.symmetry.bandstructure import HighSymmKpath
from pymatgen.core.periodic_table import Element
from bdwork.unfold import unfold, make_kpath, removeDuplicateKpoints
from pymatgen.core.periodic_table import Element
from pyprocar.utils.utilsprocar import UtilsProcar
from pyprocar.io.procarparser import ProcarParser
from functools import reduce
import matplotlib.pyplot as plt
from matplotlib.patches import Wedge
from matplotlib.collections import PatchCollection
import matplotlib.colors as colors
from matplotlib.colors import (
    Normalize,
    to_rgba,
    to_rgb,
    LinearSegmentedColormap,
)
import matplotlib.transforms as transforms
import numpy as np
import pandas as pd
import time
from copy import deepcopy
import os
from typing import Optional
from scipy.ndimage import gaussian_filter
from scipy.interpolate import interp1d

import matplotlib as mpl

mpl.rcParams.update(mpl.rcParamsDefault)


class Band:

    """
    该类包含用于从 VASP 能带结构计算结果中构建能带结构的所有方法。

    参数说明:
        folder (str): 包含 VASP 文件的文件夹路径。

        projected (bool): 是否解析 PROCAR 文件中的投影本征值。设置为 True 会增加计算时间,因此仅在需要投影能带结构时启用此选项。

        spin (str): 选择要解析的自旋方向 ('up' 或 'down')。

        kpath (str): 能带结构计算的高对称 k 点路径。由于展开 (unfolded) 计算中 KPOINTS 文件的特殊性, 此信息是展开计算中正确标注图形所必需的。对于非展开计算, 该信息可从 KPOINTS 文件中自动提取。(G 会自动转换为 \\Gamma)
        
        n (int): 每两个高对称点之间的插值点数量。该参数仅在展开计算中必需。用户应已知此数值, 因为它在生成 KPOINTS 文件时已被使用。
    """



    def __init__(
        self, #示例对象
        folder,
        projected=False,
        unfold=False,
        spin="up",
        kpath=None,
        n=None,
        M=None,
        high_symm_points=None,
        shift_efermi=0,
        interpolate=True,
        new_n=200,
        custom_kpath=None,
        soc_axis=None,
        stretch_factor=1.0,
        efermi_folder=None,
    ):

        """
        在该类生成时初始化参数

        Parameters:
            folder (str): VASP任务的文件路径

            projected (bool): 决定是否从PROCAR文件中解析投影本征值。设置为True会增加计算时间,因此仅在需要投影能带结构时使用。

            unfold (bool): 决定是否对能带结构进行展开。

            spin (str): 选择解析哪个自旋方向。('up' 或 'down')

            kpath (str): 能带结构计算中的高对称k点路径。由于展开计算中KPOINTS文件的特殊性,该信息是绘图时正确标注所必需的。对于非展开计算,该信息可从KPOINTS文件中提取。(G 会自动转换为 \\Gamma)
            
            n (int): 每两个高对称点之间的点数。仅在展开计算中需要。用户应已知该数值,因为它是在生成KPOINTS文件时使用的。
            
            M (list[list]): 用于展开计算的变换矩阵。可通过utils模块中的conver_slab函数获取。
            
            high_symm_points (list[list]): 用于展开计算的体布里渊区中高对称点的坐标。
            
            shift_efermi (float): 选项,可将费米能级按指定值进行平移。
            
            interpolate (bool): 决定是否对每两个高对称点之间的数据进行插值。
            
            new_n (int): 每两个高对称点之间的新k点数。
            
            custom_kpath (list): 自定义k路径,可按用户需求选择。例如路径G-X-W-L-G-K包含5个片段:[1 -> G-X, 2 -> X-W, 3 -> W-L, 4 -> L-G, 5 -> G-K]。如果用户只想绘制G-X-W路径,可设置custom_kpath=[1,2]。若想反转某段k路径,则应将该索引设为负值,例如目标路径为G-X|L-W,则custom_kpath=[1,-3]
            
            soc_axis (str | None): 此参数可以为None,或为'x'、'y'或'z'。若指定为'x'、'y'或'z'之一,则spin='up'态由该自旋分量的正值定义,
            
            spin='down'态由负值定义。仅用于SOC开启时显示伪自旋极化图。

            stretch_factor (float): 用于按某一常数缩放本征值。适用于与ARPES数据进行对比。默认值为stretch_factor = 1.0(即不缩放)

            efermi_folder (str | None): 包含显示E-fermi的OUTCAR文件的文件夹。默认值为None,表示与能带计算目录`folder`相同。理想情况下应设为SCF计算的工作目录。
        """







        self.interpolate = interpolate
        self.soc_axis = soc_axis
        self.new_n = new_n
        self.stretch_factor = stretch_factor

        self.eigenval = Eigenval(os.path.join(folder, "EIGENVAL"))
        # 读取 EIGENVAL 文件，并使用 Eigenval 函数分析。

        outcar_path = os.path.join(efermi_folder or folder, "OUTCAR")
        # OUTCAR 文件路径

        # 尝试从 VASP 的 OUTCAR 文件中提取费米能级（E-fermi），并加上一个可选的能量偏移 shift_efermi，最终存储在 self.efermi 中。
        try:
            efermi_output = os.popen(f"grep E-fermi {outcar_path}").read().strip()
            if not efermi_output:
                raise ValueError(f"No E-fermi value found in {outcar_path}")

            efermi_value = efermi_output.split()[2]
            self.efermi = float(efermi_value) + shift_efermi
        except (IndexError, ValueError) as e:
            raise ValueError(f"Error reading E-fermi value from {outcar_path}: {e}")

        # 读取指定路径下的 POSCAR 文件，并用 Poscar 类解析成结构对象，保存为 self.poscar。
        self.poscar = Poscar.from_file(
            os.path.join(folder, "POSCAR"),
            check_for_POTCAR=False,
            read_velocities=False,
        )


        # INCAR 文件路径
        self.incar = Incar.from_file(os.path.join(folder, "INCAR"))

        # 检查 INCAR 中的 LSORBIT 参数
        # 等价简化写法：self.lsorbit = self.incar.get("LSORBIT", False) is True
        if "LSORBIT" in self.incar:
            if self.incar["LSORBIT"]:
                self.lsorbit = True
            else:
                self.lsorbit = False
        else:
            self.lsorbit = False

        # 检查 INCAR 中的 ISPIN 参数
        if "ISPIN" in self.incar:
            if self.incar["ISPIN"] == 2:
                self.ispin = True
            else:
                self.ispin = False
        else:
            self.ispin = False

        # 检查 INCAR 中的 LHFCALC 参数（LHFCALC 决定是否开启杂化泛函 HSE 计算）
        if "LHFCALC" in self.incar:
            if self.incar["LHFCALC"]:
                self.hse = True
            else:
                self.hse = False
        else:
            self.hse = False

        # 加载 VASP 的 KPOINTS 文件
        self.kpoints_file = Kpoints.from_file(os.path.join(folder, "KPOINTS"))

        # 加载波函数文件 WAVECAR
        self.wavecar = os.path.join(folder, "WAVECAR")

        self.projected = projected

        self.forbitals = self._check_f_orb()

        self.unfold = unfold

        if self.hse and self.unfold:
            self.hse = False

        self.kpath = kpath
        self.n = n
        self.M = M
        self.high_symm_points = high_symm_points
        self.folder = folder
        self.spin = spin
        self.spin_dict = {"up": Spin.up, "down": Spin.down}

        # 处理普通能带计算和能带展开计算的两种情况
        '''
        eigenvalues.npy 和 unfolded_eigenvalues.npy 两个文件是预加载文件，可以加快运行的速度。
        '''
        if not self.unfold:
            self.pre_loaded_bands = os.path.isfile(
                os.path.join(folder, "eigenvalues.npy")
            )
            self.eigenvalues, self.kpoints = self._load_bands()
        else:
            self.pre_loaded_bands = os.path.isfile(
                os.path.join(folder, "unfolded_eigenvalues.npy")
            )
            (
                self.eigenvalues,
                self.spectral_weights,
                self.K_indices,
                self.kpoints,
            ) = self._load_bands_unfold()

        # 根据 stretch_factor 调整能量本征值 (eigenvalues)
        if self.stretch_factor != 1.0:
            self.eigenvalues *= self.stretch_factor

        # 写入各种颜色列表
        self.color_dict = {
            0: "#FF0000",
            1: "#0000FF",
            2: "#008000",
            3: "#800080",
            4: "#E09200",
            5: "#FF5C77",
            6: "#778392",
            7: "#07C589",
            8: "#40BAF2",
            9: "#FF0000",
            10: "#0000FF",
            11: "#008000",
            12: "#800080",
            13: "#E09200",
            14: "#FF5C77",
            15: "#778392",
        }

        # 分轨道列表
        self.orbital_labels = {
            0: "s",
            1: "p_{y}",
            2: "p_{z}",
            3: "p_{x}",
            4: "d_{xy}",
            5: "d_{yz}",
            6: "d_{z^{2}}",
            7: "d_{xz}",
            8: "d_{x^{2}-y^{2}}",
            9: "f_{y^{3}x^{2}}",
            10: "f_{xyz}",
            11: "f_{yz^{2}}",
            12: "f_{z^{3}}",
            13: "f_{xz^{2}}",
            14: "f_{zx^{3}}",
            15: "f_{x^{3}}",
        }
        self.spd_relations = {
            "s": 0,
            "p": 1,
            "d": 2,
            "f": 3,
        }

        # 设置能带中 k 点路径
        self.custom_kpath = custom_kpath
        
        if self.custom_kpath is not None:
            (
                self.custom_kpath_inds,
                self.custom_kpath_flip,
            ) = self._get_custom_kpath()

        if projected:
            self.pre_loaded_projections = os.path.isfile(
                os.path.join(folder, "projected_eigenvalues.npy")
            )
            self.projected_eigenvalues = self._load_projected_bands()

        if soc_axis is not None and self.lsorbit:
            self.pre_loaded_spin_projections = os.path.isfile(
                os.path.join(folder, "spin_projections.npy")
            )
            self.spin_projections = self._load_soc_spin_projection()




    def _get_custom_kpath(self):
        flip = (-np.sign(self.custom_kpath) + 1).astype(bool)
        inds = (np.abs(self.custom_kpath) - 1).astype(int)

        return inds, flip


    # 检查是否含有 La 系稀土元素（是否含有 f 轨道）
    def _check_f_orb(self):
        f_elements = [
            "La",
            "Ac",
            "Ce",
            "Tb",
            "Th",
            "Pr",
            "Dy",
            "Pa",
            "Nd",
            "Ho",
            "U",
            "Pm",
            "Er",
            "Np",
            "Sm",
            "Tm",
            "Pu",
            "Eu",
            "Yb",
            "Am",
            "Gd",
            "Lu",
        ]
        f = False
        for element in self.poscar.site_symbols:
            if element in f_elements:
                f = True

        return f


    def _load_bands(self):

        """
        该函数用于从 vasprun.xml 文件中加载本征值，并将其存储到一个字典中，
        该字典的结构为：带索引 --> 本征值

        返回值：
            bands_dict (dict[str][np.ndarray]): 包含每个能带对应本征值的字典
        """

        if self.spin == "up":
            spin = 0
        if self.spin == "down":
            spin = 1

        if self.pre_loaded_bands:
            with open(os.path.join(self.folder, "eigenvalues.npy"), "rb") as eigenvals:
                band_data = np.load(eigenvals)

            if self.ispin and not self.lsorbit:
                eigenvalues = band_data[:, :, [0, 2]]
                kpoints = band_data[0, :, 4:]
            else:
                eigenvalues = band_data[:, :, 0]
                kpoints = band_data[0, :, 2:]
        else:
            if len(self.eigenval.eigenvalues.keys()) > 1:
                # 处理有自旋极化的情况
                eigenvalues_up = np.transpose(
                    self.eigenval.eigenvalues[Spin.up], axes=(1, 0, 2)
                )
                eigenvalues_down = np.transpose(
                    self.eigenval.eigenvalues[Spin.down], axes=(1, 0, 2)
                )
                # 费米能级校正
                eigenvalues_up[:, :, 0] = eigenvalues_up[:, :, 0] - self.efermi
                eigenvalues_down[:, :, 0] = eigenvalues_down[:, :, 0] - self.efermi
                # 拼接自旋上下的能带数据
                eigenvalues = np.concatenate([eigenvalues_up, eigenvalues_down], axis=2)
            else:
                # 无自旋极化
                eigenvalues = np.transpose(
                    self.eigenval.eigenvalues[Spin.up], axes=(1, 0, 2)
                )
                eigenvalues[:, :, 0] = eigenvalues[:, :, 0] - self.efermi

            # 处理 k 点的信息
            kpoints = np.array(self.eigenval.kpoints)
            # 只保留权重为0的 k 点，这通常是 HSE 计算中用于计算能带结构的数据点。
            if self.hse:
                kpoint_weights = np.array(self.eigenval.kpoints_weights)
                zero_weight = np.where(kpoint_weights == 0)[0]
                eigenvalues = eigenvalues[:, zero_weight]
                kpoints = kpoints[zero_weight]

            # 数据保存为缓存文件
            band_data = np.append(
                eigenvalues,
                np.tile(kpoints, (eigenvalues.shape[0], 1, 1)), # 难点
                axis=2,
            )

            np.save(os.path.join(self.folder, "eigenvalues.npy"), band_data)

            if len(self.eigenval.eigenvalues.keys()) > 1:
                eigenvalues = eigenvalues[:, :, [0, 2]]
            else:
                eigenvalues = eigenvalues[:, :, 0]

        if len(self.eigenval.eigenvalues.keys()) > 1:
            eigenvalues = eigenvalues[:, :, spin]

        return eigenvalues, kpoints

    def _load_bands_unfold(self):
        '''
        这段代码实现了一个私有方法 _load_bands_unfold()，用于加载能带展开 (Band Unfolding) 的数据。Band Unfolding 是在电子结构计算中，将复杂材料的能带结构映射到一个简单的、类似于理想晶体的布里渊区，以便更清晰地展示电子态的起源。这个方法特别用于处理含有杂质、缺陷、异质结或超晶胞结构的系统。
        '''

        '''
        自旋 "up" (spin = 0)

        自旋 "down" (spin = 1,如果没有自旋轨道耦合 lsorbit=False)

        自旋轨道耦合 (SOC) 情况下只需要考虑 spin = 0,因为自旋态混合。
        '''
        if self.spin == "up":
            spin = 0
        if self.spin == "down":
            if self.lsorbit:
                spin = 0
            else:
                spin = 1

        # k路径 (k-path) 生成
        kpath = make_kpath(self.high_symm_points, nseg=self.n)

        # 预加载的能带数据
        if self.pre_loaded_bands:
            with open(
                os.path.join(self.folder, "unfolded_eigenvalues.npy"), "rb"
            ) as eigenvals:
                band_data = np.load(eigenvals)
        else:
            # 从 WAVECAR 展开能带
            wavecar_data = unfold(
                M=self.M,
                wavecar=self.wavecar,
                lsorbit=self.lsorbit,
            )
            band_data = wavecar_data.spectral_weight(kpath)
            np.save(
                os.path.join(self.folder, "unfolded_eigenvalues.npy"),
                band_data,
            )

        # 数据重排和自旋选择
        band_data = np.transpose(band_data[spin], axes=(2, 1, 0))
        eigenvalues, spectral_weights, K_indices = band_data

        # 费米能级校正
        eigenvalues = eigenvalues - self.efermi

        # k 路径点的处理
        kpath = np.array(kpath)
        path_len = len(self.kpath)
        n = self.n
        inserts = [n * (i + 1) for i in range(path_len - 1)]
        inds = list(range(n * path_len + 1))
        for i in reversed(inserts):
            inds.insert(i, i)

        # 应用重排到实际数据
        kpath = kpath[inds]
        spectral_weights = spectral_weights[:, inds]
        K_indices = K_indices[:, inds]
        eigenvalues = eigenvalues[:, inds]

        return eigenvalues, spectral_weights, K_indices, kpath

    def _load_projected_bands(self):
        """
        该函数从 vasprun.xml 文件中加载每个能带中轨道的投影权重，
        并将其存储在一个字典中，字典的结构为：
        带索引 --> 原子索引 --> 轨道的投影权重

        返回值：
            projected_dict (dict[str][int][pd.DataFrame]): 
                包含每个能带中，每个原子上所有轨道投影权重的字典。
        """


        if self.lsorbit:
            if self.soc_axis is None:
                spin = 0
            elif self.soc_axis == "x":
                spin = 1
            elif self.soc_axis == "y":
                spin = 2
            elif self.soc_axis == "z":
                spin = 3
        else:
            if self.spin == "up":
                spin = 0
            elif self.spin == "down":
                spin = 1

        if not os.path.isfile(os.path.join(self.folder, "PROCAR_repaired")):
            UtilsProcar().ProcarRepair(
                os.path.join(self.folder, "PROCAR"),
                os.path.join(self.folder, "PROCAR_repaired"),
            )

        if self.pre_loaded_projections:
            with open(
                os.path.join(self.folder, "projected_eigenvalues.npy"), "rb"
            ) as projected_eigenvals:
                projected_eigenvalues = np.load(projected_eigenvals)
        else:
            parser = ProcarParser()
            parser.readFile(os.path.join(self.folder, "PROCAR_repaired"))
            if self.ispin and not self.lsorbit and np.sum(self.poscar.natoms) == 1:
                shape = int(parser.spd.shape[1] / 2)
                projected_eigenvalues_up = np.transpose(
                    parser.spd[:, :shape, 0, :, 1:-1], axes=(1, 0, 2, 3)
                )
                projected_eigenvalues_down = np.transpose(
                    parser.spd[:, shape:, 0, :, 1:-1], axes=(1, 0, 2, 3)
                )
                projected_eigenvalues = np.concatenate(
                    [
                        projected_eigenvalues_up[:, :, :, :, np.newaxis],
                        projected_eigenvalues_down[:, :, :, :, np.newaxis],
                    ],
                    axis=4,
                )
                projected_eigenvalues = np.transpose(
                    projected_eigenvalues, axes=(0, 1, 4, 2, 3)
                )
            elif self.ispin and not self.lsorbit and np.sum(self.poscar.natoms) != 1:
                shape = int(parser.spd.shape[1] / 2)
                projected_eigenvalues_up = np.transpose(
                    parser.spd[:, :shape, 0, :-1, 1:-1], axes=(1, 0, 2, 3)
                )
                projected_eigenvalues_down = np.transpose(
                    parser.spd[:, shape:, 0, :-1, 1:-1], axes=(1, 0, 2, 3)
                )
                projected_eigenvalues = np.concatenate(
                    [
                        projected_eigenvalues_up[:, :, :, :, np.newaxis],
                        projected_eigenvalues_down[:, :, :, :, np.newaxis],
                    ],
                    axis=4,
                )
                projected_eigenvalues = np.transpose(
                    projected_eigenvalues, axes=(0, 1, 4, 2, 3)
                )
            else:
                if np.sum(self.poscar.natoms) == 1:
                    projected_eigenvalues = np.transpose(
                        parser.spd[:, :, :, :, 1:-1], axes=(1, 0, 2, 3, 4)
                    )
                else:
                    projected_eigenvalues = np.transpose(
                        parser.spd[:, :, :, :-1, 1:-1], axes=(1, 0, 2, 3, 4)
                    )

            np.save(
                os.path.join(self.folder, "projected_eigenvalues.npy"),
                projected_eigenvalues,
            )

        projected_eigenvalues = projected_eigenvalues[:, :, spin, :, :]

        if self.lsorbit and self.soc_axis is not None:
            separated_projections = np.zeros(projected_eigenvalues.shape + (2,))
            separated_projections[projected_eigenvalues > 0, 0] = projected_eigenvalues[
                projected_eigenvalues > 0
            ]
            separated_projections[projected_eigenvalues < 0, 1] = (
                -projected_eigenvalues[projected_eigenvalues < 0]
            )

            if self.spin == "up":
                soc_spin = 0
            elif self.spin == "down":
                soc_spin = 1

            projected_eigenvalues = separated_projections[..., soc_spin]

        if self.hse:
            kpoint_weights = np.array(self.eigenval.kpoints_weights)
            zero_weight = np.where(kpoint_weights == 0)[0]
            projected_eigenvalues = projected_eigenvalues[:, zero_weight]

        projected_eigenvalues = np.square(projected_eigenvalues)

        return projected_eigenvalues

    def _load_soc_spin_projection(self):
        """
        This function loads the project weights of the orbitals in each band
        from vasprun.xml into a dictionary of the form:
        band index --> atom index --> weights of orbitals

        Returns:
            projected_dict (dict([str][int][pd.DataFrame])): Dictionary containing the projected weights of all orbitals on each atom for each band.
        """

        if not self.lsorbit:
            raise BaseException(
                f"You selected soc_axis='{self.soc_axis}' for a non-soc axis calculation, please set soc_axis=None"
            )
        if self.lsorbit and self.soc_axis == "x":
            spin = 1
        if self.lsorbit and self.soc_axis == "y":
            spin = 2
        if self.lsorbit and self.soc_axis == "z":
            spin = 3

        if not os.path.isfile(os.path.join(self.folder, "PROCAR_repaired")):
            UtilsProcar().ProcarRepair(
                os.path.join(self.folder, "PROCAR"),
                os.path.join(self.folder, "PROCAR_repaired"),
            )

        if self.pre_loaded_spin_projections:
            with open(
                os.path.join(self.folder, "spin_projections.npy"), "rb"
            ) as spin_projs:
                spin_projections = np.load(spin_projs)
        else:
            parser = ProcarParser()
            parser.readFile(os.path.join(self.folder, "PROCAR_repaired"))
            spin_projections = np.transpose(parser.spd[:, :, :, -1, -1], axes=(1, 0, 2))

            np.save(
                os.path.join(self.folder, "spin_projections.npy"),
                spin_projections,
            )

        spin_projections = spin_projections[:, :, spin]

        if self.hse:
            kpoint_weights = np.array(self.eigenval.kpoints_weights)
            zero_weight = np.where(kpoint_weights == 0)[0]
            spin_projections = spin_projections[:, zero_weight]

        separated_projections = np.zeros(
            (spin_projections.shape[0], spin_projections.shape[1], 2)
        )
        separated_projections[spin_projections > 0, 0] = spin_projections[
            spin_projections > 0
        ]
        separated_projections[spin_projections < 0, 1] = -spin_projections[
            spin_projections < 0
        ]

        separated_projections = separated_projections / separated_projections.max()

        if self.spin == "up":
            separated_projections = separated_projections[:, :, 0]
        elif self.spin == "down":
            separated_projections = separated_projections[:, :, 1]
        else:
            raise BaseException("The soc_axis feature does not work with spin='both'")

        return separated_projections

    def _sum_spd(self, spd):
        """
        This function sums the weights of the s, p, and d orbitals for each atom
        and creates a dictionary of the form:
        band index --> s,p,d orbital weights

        Returns:
            spd_dict (dict([str][pd.DataFrame])): Dictionary that contains the summed weights for the s, p, and d orbitals for each band
        """

        if not self.forbitals:
            spd_indices = [np.array([False for _ in range(9)]) for i in range(3)]
            spd_indices[0][0] = True
            spd_indices[1][1:4] = True
            spd_indices[2][4:] = True
        else:
            spd_indices = [np.array([False for _ in range(16)]) for i in range(4)]
            spd_indices[0][0] = True
            spd_indices[1][1:4] = True
            spd_indices[2][4:9] = True
            spd_indices[3][9:] = True

        orbital_contributions = np.sum(self.projected_eigenvalues, axis=2)

        spd_contributions = np.transpose(
            np.array(
                [
                    np.sum(orbital_contributions[:, :, ind], axis=2)
                    for ind in spd_indices
                ]
            ),
            axes=[1, 2, 0],
        )

        #  norm_term = np.sum(spd_contributions, axis=2)[:,:,np.newaxis]
        #  spd_contributions = np.divide(spd_contributions, norm_term, out=np.zeros_like(spd_contributions), where=norm_term!=0)

        spd_contributions = spd_contributions[
            :, :, [self.spd_relations[orb] for orb in spd]
        ]

        return spd_contributions

    def _sum_orbitals(self, orbitals):
        """
        This function finds the weights of desired orbitals for all atoms and
            returns a dictionary of the form:
            band index --> orbital index

        Parameters:
            orbitals (list): List of desired orbitals.
                0 = s
                1 = py
                2 = pz
                3 = px
                4 = dxy
                5 = dyz
                6 = dz2
                7 = dxz
                8 = dx2-y2
                9 = fy3x2
                10 = fxyz
                11 = fyz2
                12 = fz3
                13 = fxz2
                14 = fzx3
                15 = fx3

        Returns:
            orbital_dict (dict[str][pd.DataFrame]): Dictionary that contains the projected weights of the selected orbitals.
        """
        orbital_contributions = self.projected_eigenvalues.sum(axis=2)
        #  norm_term =  np.sum(orbital_contributions, axis=2)[:,:,np.newaxis]
        #  orbital_contributions = np.divide(orbital_contributions, norm_term, out=np.zeros_like(orbital_contributions), where=norm_term!=0)
        orbital_contributions = orbital_contributions[:, :, [orbitals]]

        return orbital_contributions

    def _sum_atoms(self, atoms, spd=False):
        """
        This function finds the weights of desired atoms for all orbitals and
            returns a dictionary of the form:
            band index --> atom index

        Parameters:
            atoms (list): List of desired atoms where atom 0 is the first atom in
                the POSCAR file.

        Returns:
            atom_dict (dict[str][pd.DataFrame]): Dictionary that contains the projected
                weights of the selected atoms.
        """

        if spd:
            if not self.forbitals:
                spd_indices = [np.array([False for _ in range(9)]) for i in range(3)]
                spd_indices[0][0] = True
                spd_indices[1][1:4] = True
                spd_indices[2][4:] = True
            else:
                spd_indices = [np.array([False for _ in range(16)]) for i in range(4)]
                spd_indices[0][0] = True
                spd_indices[1][1:4] = True
                spd_indices[2][4:9] = True
                spd_indices[3][9:] = True

            atoms_spd = np.transpose(
                np.array(
                    [
                        np.sum(self.projected_eigenvalues[:, :, :, ind], axis=3)
                        for ind in spd_indices
                    ]
                ),
                axes=(1, 2, 3, 0),
            )

            #  atoms_spd = atoms_spd[:,:,[atoms], :]

            #  norm_term = np.sum(atoms_spd_to_norm, axis=(2,3))[:,:, np.newaxis]
            #  atoms_spd = np.divide(atoms_spd, norm_term, out=np.zeros_like(atoms_spd), where=norm_term!=0)

            return atoms_spd
        else:
            atoms_array = self.projected_eigenvalues.sum(axis=3)
            #  norm_term = np.sum(atoms_array, axis=2)[:,:,np.newaxis]
            #  atoms_array = np.divide(atoms_array, norm_term, out=np.zeros_like(atoms_array), where=norm_term!=0)
            atoms_array = atoms_array[:, :, [atoms]]

            return atoms_array

    def _sum_elements(self, elements, orbitals=False, spd=False, spd_options=None):
        """
        This function sums the weights of the orbitals of specific elements within the
        calculated structure and returns a dictionary of the form:
        band index --> element label --> orbital weights for orbitals = True
        band index --> element label for orbitals = False
        This is useful for structures with many elements because manually entering indicies is
        not practical for large structures.

        Parameters:
            elements (list): List of element symbols to sum the weights of.
            orbitals (bool): Determines whether or not to inclue orbitals or not
                (True = keep orbitals, False = sum orbitals together )
            spd (bool): Determines whether or not to sum the s, p, and d orbitals


        Returns:
            element_dict (dict([str][str][pd.DataFrame])): Dictionary that contains the summed weights for each orbital for a given element in the structure.
        """

        poscar = self.poscar
        natoms = poscar.natoms
        symbols = poscar.site_symbols
        projected_eigenvalues = self.projected_eigenvalues

        element_list = np.hstack(
            [[symbols[i] for j in range(natoms[i])] for i in range(len(symbols))]
        )

        element_indices = [
            np.where(np.isin(element_list, element))[0] for element in elements
        ]

        element_orbitals = np.transpose(
            np.array(
                [
                    np.sum(projected_eigenvalues[:, :, ind, :], axis=2)
                    for ind in element_indices
                ]
            ),
            axes=(1, 2, 0, 3),
        )

        if orbitals:
            return element_orbitals
        elif spd:
            if not self.forbitals:
                spd_indices = [np.array([False for _ in range(9)]) for i in range(3)]
                spd_indices[0][0] = True
                spd_indices[1][1:4] = True
                spd_indices[2][4:] = True
            else:
                spd_indices = [np.array([False for _ in range(16)]) for i in range(4)]
                spd_indices[0][0] = True
                spd_indices[1][1:4] = True
                spd_indices[2][4:9] = True
                spd_indices[3][9:] = True

            element_spd = np.transpose(
                np.array(
                    [
                        np.sum(element_orbitals[:, :, :, ind], axis=3)
                        for ind in spd_indices
                    ]
                ),
                axes=(1, 2, 3, 0),
            )

            #  norm_term = np.sum(element_spd, axis=(2,3))[:,:,np.newaxis, np.newaxis]
            #  element_spd = np.divide(element_spd, norm_term, out=np.zeros_like(element_spd), where=norm_term!=0)

            return element_spd
        else:
            element_array = np.sum(element_orbitals, axis=3)
            #  norm_term = np.sum(element_array, axis=2)[:,:,np.newaxis]
            #  element_array = np.divide(element_array, norm_term, out=np.zeros_like(element_array), where=norm_term!=0)

            return element_array

    def _get_k_distance_old(self):
        cell = self.poscar.structure.lattice.matrix
        kpt_c = np.dot(self.kpoints, np.linalg.inv(cell).T)
        kdist = np.r_[0, np.cumsum(np.linalg.norm(np.diff(kpt_c, axis=0), axis=1))]

        return kdist

    def _get_k_distance(self):
        slices = self._get_slices(unfold=self.unfold, hse=self.hse)
        kdists = []

        if self.custom_kpath is not None:
            #  if self.custom_kpath is None:
            index = self.custom_kpath_inds
        else:
            index = range(len(slices))

        for j, i in enumerate(index):
            inv_cell = deepcopy(self.poscar.structure.lattice.inv_matrix)
            inv_cell_norms = np.linalg.norm(inv_cell, axis=1)
            inv_cell /= inv_cell_norms.min()

            # If you want to be able to compare only identical relative cell lengths
            kpt_c = np.dot(self.kpoints[slices[i]], inv_cell.T)

            # If you want to be able to compare any cell length. Maybe straining an orthorhombic cell or something like that
            # This will mess up relative distances though
            # kpt_c = self.kpoints[slices[i]]
            kdist = np.r_[0, np.cumsum(np.linalg.norm(np.diff(kpt_c, axis=0), axis=1))]
            if j == 0:
                kdists.append(kdist)
            else:
                kdists.append(kdist + kdists[-1][-1])

        # kdists = np.array(kdists)

        return kdists

    def _get_kticks(self, ax, wave_vectors, vlinecolor):
        """
        This function extracts the kpoint labels and index locations for a regular
        band structure calculation (non unfolded).

        Parameters:
            ax (matplotlib.pyplot.axis): Axis to append the tick labels
        """

        high_sym_points = self.kpoints_file.kpts

        segements = []
        for i in range(0, len(high_sym_points) - 1):
            if not i % 2:
                segements.append([i, i + 1])

        if self.custom_kpath is not None:
            high_sym_points_inds = []
            for i, b in zip(self.custom_kpath_inds, self.custom_kpath_flip):
                if b:
                    seg = list(reversed(segements[i]))
                else:
                    seg = segements[i]

                high_sym_points_inds.extend(seg)
        else:
            high_sym_points_inds = list(range(len(high_sym_points)))

        num_kpts = self.kpoints_file.num_kpts
        kpts_labels = np.array(
            [f"${k}$" if k != "G" else "$\\Gamma$" for k in self.kpoints_file.labels]
        )
        all_kpoints = self.kpoints

        group_index = []
        for i, j in enumerate(high_sym_points_inds):
            if i == 0:
                group_index.append([j])
            if i % 2 and not i == len(high_sym_points_inds) - 1:
                group_index.append([j, high_sym_points_inds[i + 1]])
            if i == len(high_sym_points_inds) - 1:
                group_index.append([j])

        labels = []
        index = []

        for i in group_index:
            if len(i) == 1:
                labels.append(kpts_labels[i[0]])
                index.append(i[0])
            else:
                if kpts_labels[i[0]] == kpts_labels[i[1]]:
                    labels.append(kpts_labels[i[0]])
                    index.append(i[0])
                else:
                    merged_label = "|".join(
                        [
                            kpts_labels[i[0]],
                            kpts_labels[i[1]],
                        ]
                    ).replace("$|$", "|")
                    labels.append(merged_label)
                    index.append(i[0])

        kpoints_index = [0] + [
            (i + 1) * num_kpts - 1
            for i in range(int((len(high_sym_points_inds) + 1) / 2))
        ]

        for k in kpoints_index:
            ax.axvline(x=wave_vectors[k], color=vlinecolor, alpha=0.7, linewidth=0.5)

        ax.set_xticks([wave_vectors[k] for k in kpoints_index])
        ax.set_xticklabels(labels)

    def _get_kticks_hse(self, wave_vectors, ax, kpath, vlinecolor):
        structure = self.poscar.structure
        kpath_obj = HighSymmKpath(structure)
        kpath_labels = np.array(list(kpath_obj._kpath["kpoints"].keys()))
        kpath_coords = np.array(list(kpath_obj._kpath["kpoints"].values()))
        index = np.where(
            np.isclose(
                self.kpoints[:, None],
                kpath_coords,
            )
            .all(-1)
            .any(-1)
            == True
        )[0]

        segements = []
        for i in range(0, len(index) - 1):
            if not i % 2:
                segements.append([index[i], index[i + 1]])

        if self.custom_kpath is not None:
            high_sym_points_inds = []
            for i, b in zip(self.custom_kpath_inds, self.custom_kpath_flip):
                if b:
                    seg = list(reversed(segements[i]))
                else:
                    seg = segements[i]

                high_sym_points_inds.extend(seg)
        else:
            high_sym_points_inds = list(np.concatenate(segements))

        full_segments = []
        for i in range(0, len(high_sym_points_inds) - 1):
            if not i % 2:
                full_segments.append(
                    [high_sym_points_inds[i], high_sym_points_inds[i + 1]]
                )

        segment_lengths = [np.abs(i[1] - i[0]) + 1 for i in full_segments]
        kpoints_index = [0] + [
            np.sum(segment_lengths[:i]) for i in range(1, len(segment_lengths) + 1)
        ]
        kpoints_index[-1] -= 1

        group_index = []
        for i, j in enumerate(high_sym_points_inds):
            if i == 0:
                group_index.append([j])
            if i % 2 and not i == len(high_sym_points_inds) - 1:
                group_index.append([j, high_sym_points_inds[i + 1]])
            if i == len(high_sym_points_inds) - 1:
                group_index.append([j])

        kpoints_in_band = []
        for group in group_index:
            g = [self.kpoints[g] for g in group]
            kpoints_in_band.append(g)

        group_labels = []
        for kpoints in kpoints_in_band:
            group = []
            for k in kpoints:
                for i, coords in enumerate(kpath_coords):
                    if (np.round(k, 5) == np.round(coords, 5)).all():
                        group.append(kpath_labels[i])
            group_labels.append(group)

        labels = []
        index = []

        for label in group_labels:
            if len(label) == 1:
                labels.append(label[0])
            else:
                if label[0] == label[1]:
                    labels.append(label[0])
                else:
                    merged_label = "|".join(
                        [
                            label[0],
                            label[1],
                        ]
                    ).replace("$|$", "|")
                    labels.append(merged_label)

        kpath = [f"${k}$" if k != "G" else "$\\Gamma$" for k in labels]

        for k in kpoints_index:
            ax.axvline(x=wave_vectors[k], color=vlinecolor, alpha=0.7, linewidth=0.5)

        ax.set_xticks([wave_vectors[k] for k in kpoints_index], kpath)

    def _get_kticks_unfold(self, ax, wave_vectors, vlinecolor):
        if self.custom_kpath is not None:
            kpath = []
            for i, b in zip(self.custom_kpath_inds, self.custom_kpath_flip):
                if b:
                    seg = list(reversed(self.kpath[i]))
                else:
                    seg = self.kpath[i]

                kpath.extend(seg)
        else:
            kpath = []
            for seg in self.kpath:
                kpath.extend(seg)

        kpath = [f"${k.strip()}$" if k.strip() != "G" else "$\\Gamma$" for k in kpath]

        group_kpath = []
        for i, j in enumerate(kpath):
            if i == 0:
                group_kpath.append([j])
            if i % 2 and not i == len(kpath) - 1:
                group_kpath.append([j, kpath[i + 1]])
            if i == len(kpath) - 1:
                group_kpath.append([j])

        labels = []

        for k in group_kpath:
            if len(k) == 1:
                labels.append(k[0])
            else:
                if k[0] == k[1]:
                    labels.append(k[0])
                else:
                    merged_label = "|".join([k[0], k[1]]).replace("$|$", "|")
                    labels.append(merged_label)

        n = int(len(self.kpoints) / len(self.kpath))
        kpoints_index = [0] + [(n * i) for i in range(1, len(labels))]
        kpoints_index[-1] -= 1

        for k in kpoints_index:
            ax.axvline(x=wave_vectors[k], color=vlinecolor, alpha=0.7, linewidth=0.5)

        ax.set_xticks(wave_vectors[kpoints_index])
        ax.set_xticklabels(labels)
        #  plt.xticks(np.array(kpoints)[kpoints_index], kpath)

    def _get_kticks_unfold_old(self, ax, wave_vectors, vlinecolor):
        if type(self.kpath) == str:
            kpath = [
                f"${k}$" if k != "G" else "$\\Gamma$"
                for k in self.kpath.upper().strip()
            ]
        elif type(self.kpath) == list:
            kpath = self.kpath

        kpoints_index = [0] + [(self.n * i) for i in range(1, len(self.kpath))]
        kpoints_index[-1] -= 1

        for k in kpoints_index:
            ax.axvline(x=wave_vectors[k], color=vlinecolor, alpha=0.7, linewidth=0.5)

        ax.set_xticks(wave_vectors[kpoints_index])
        ax.set_xticklabels(kpath)
        #  plt.xticks(np.array(kpoints)[kpoints_index], kpath)

    def _get_kticks_old(self, ax, wave_vectors, vlinecolor):
        """
        This function extracts the kpoint labels and index locations for a regular
        band structure calculation (non unfolded).

        Parameters:
            ax (matplotlib.pyplot.axis): Axis to append the tick labels
        """

        high_sym_points = self.kpoints_file.kpts
        kpts_labels = np.array(
            [f"${k}$" if k != "G" else "$\\Gamma$" for k in self.kpoints_file.labels]
        )
        all_kpoints = self.kpoints

        index = [0]
        for i in range(len(high_sym_points) - 2):
            if high_sym_points[i + 2] != high_sym_points[i + 1]:
                index.append(i)
        index.append(len(high_sym_points) - 1)

        kpts_loc = np.isin(np.round(all_kpoints, 3), np.round(high_sym_points, 3)).all(
            1
        )
        kpoints_index = np.where(kpts_loc == True)[0]

        kpts_labels = kpts_labels[index]
        kpoints_index = list(kpoints_index[index])
        #  kpoints_index = ax.lines[0].get_xdata()[kpoints_index]

        for k in kpoints_index:
            ax.axvline(x=wave_vectors[k], color=vlinecolor, alpha=0.7, linewidth=0.5)

        ax.set_xticks([wave_vectors[k] for k in kpoints_index])
        ax.set_xticklabels(kpts_labels)

    def _get_kticks_hse_old(self, wave_vectors, ax, kpath, vlinecolor):
        structure = self.poscar.structure
        kpath_obj = HighSymmKpath(structure)
        kpath_labels = np.array(list(kpath_obj._kpath["kpoints"].keys()))
        kpath_coords = np.array(list(kpath_obj._kpath["kpoints"].values()))
        index = np.where(
            np.isclose(
                self.kpoints[:, None],
                kpath_coords,
            )
            .all(-1)
            .any(-1)
            == True
        )[0]
        #  index = np.where(np.isclose(self.kpoints[:, None], kpath_coords).all(-1).any(-1) == True)[0]
        #  index = np.where((self.kpoints[:, None] == kpath_coords).all(-1).any(-1) == True)[0]
        index = (
            [index[0]]
            + [index[i] for i in range(1, len(index) - 1) if i % 2]
            + [index[-1]]
        )
        kpoints_in_band = self.kpoints[index]

        label_index = []
        for i in range(kpoints_in_band.shape[0]):
            for j in range(kpath_coords.shape[0]):
                if (
                    np.round(kpoints_in_band[i], 5) == np.round(kpath_coords[j], 5)
                ).all():
                    label_index.append(j)

        kpoints_index = index
        kpath = kpath_labels[label_index]
        #  kpoints_index = ax.lines[0].get_xdata()[kpoints_index]

        kpath = [f"${k}$" if k != "G" else "$\\Gamma$" for k in kpath]

        for k in kpoints_index:
            ax.axvline(x=wave_vectors[k], color=vlinecolor, alpha=0.7, linewidth=0.5)

        plt.xticks([wave_vectors[k] for k in kpoints_index], kpath)

    def _get_slices(self, unfold=False, hse=False):
        if not unfold and not hse:
            high_sym_points = self.kpoints_file.kpts
            all_kpoints = self.kpoints
            num_kpts = self.kpoints_file.num_kpts
            num_slices = int(len(high_sym_points) / 2)
            slices = [
                slice(i * num_kpts, (i + 1) * num_kpts, None) for i in range(num_slices)
            ]

        if hse and not unfold:
            structure = self.poscar.structure
            kpath_obj = HighSymmKpath(structure)
            kpath_coords = np.array(list(kpath_obj._kpath["kpoints"].values()))
            index = np.where(
                np.isclose(
                    self.kpoints[:, None],
                    kpath_coords,
                )
                .all(-1)
                .any(-1)
                == True
            )[0]

            segements = []
            for i in range(0, len(index) - 1):
                if not i % 2:
                    segements.append([index[i], index[i + 1]])

            # print(segements)

            num_kpts = int(len(self.kpoints) / (len(index) / 2))
            slices = [
                slice(i * num_kpts, (i + 1) * num_kpts, None)
                for i in range(int(len(index) / 2))
            ]
            # print(slices)
            slices = [slice(i[0], i[1] + 1, None) for i in segements]
            # print(slices)

        if unfold and not hse:
            n = int(len(self.kpoints) / len(self.kpath))
            slices = [
                slice(i * n, (i + 1) * n, None) for i in range(int(len(self.kpath)))
            ]

        return slices

    def _get_slices_old(self, unfold=False, hse=False):
        if not unfold and not hse:
            high_sym_points = self.kpoints_file.kpts
            all_kpoints = self.kpoints
            num_kpts = self.kpoints_file.num_kpts

            if self.custom_kpath is not None:
                num_slices = len(self.custom_kpath_inds)
            else:
                num_slices = int(len(high_sym_points) / 2)

            slices = [
                slice(i * num_kpts, (i + 1) * num_kpts, None) for i in range(num_slices)
            ]

        if hse and not unfold:
            structure = self.poscar.structure
            kpath_obj = HighSymmKpath(structure)
            kpath_coords = np.array(list(kpath_obj._kpath["kpoints"].values()))
            index = np.where(
                np.isclose(
                    self.kpoints[:, None],
                    kpath_coords,
                )
                .all(-1)
                .any(-1)
                == True
            )[0]

            num_kpts = int(len(self.kpoints) / (len(index) / 2))
            slices = [
                slice(i * num_kpts, (i + 1) * num_kpts, None)
                for i in range(int(len(index) / 2))
            ]

        if unfold and not hse:
            n = int(len(self.kpoints) / len(self.kpath))
            print(n)
            slices = [
                slice(i * n, (i + 1) * n, None) for i in range(int(len(self.kpath) - 1))
            ]
            print(slices)

        return slices

    def _get_interpolated_data_segment(
        self, wave_vectors, data, crop_zero=False, kind="cubic"
    ):
        data_shape = data.shape

        if len(data_shape) == 1:
            fs = interp1d(wave_vectors, data, kind=kind, axis=0)
        else:
            fs = interp1d(wave_vectors, data, kind=kind, axis=1)

        new_wave_vectors = np.linspace(
            wave_vectors.min(), wave_vectors.max(), self.new_n
        )
        data = fs(new_wave_vectors)

        if crop_zero:
            data[np.where(data < 0)] = 0

        return new_wave_vectors, data

    def _get_interpolated_data(self, wave_vectors, data, crop_zero=False, kind="cubic"):
        slices = self._get_slices(unfold=self.unfold, hse=self.hse)
        data_shape = data.shape
        if len(data_shape) == 1:
            data = [data[i] for i in slices]
        else:
            data = [data[:, i] for i in slices]

        wave_vectors = [wave_vectors[i] for i in slices]

        if len(data_shape) == 1:
            fs = [
                interp1d(i, j, kind=kind, axis=0) for (i, j) in zip(wave_vectors, data)
            ]
        else:
            fs = [
                interp1d(i, j, kind=kind, axis=1) for (i, j) in zip(wave_vectors, data)
            ]

        new_wave_vectors = [
            np.linspace(wv.min(), wv.max(), self.new_n) for wv in wave_vectors
        ]
        data = np.hstack([f(wv) for (f, wv) in zip(fs, new_wave_vectors)])
        wave_vectors = np.hstack(new_wave_vectors)

        if crop_zero:
            data[np.where(data < 0)] = 0

        return wave_vectors, data

    def _filter_bands(self, erange):
        eigenvalues = self.eigenvalues
        where = (eigenvalues >= np.min(erange) - 1) & (
            eigenvalues <= np.max(erange) + 1
        )
        is_true = np.sum(np.isin(where, True), axis=1)
        bands_in_plot = is_true > 0

        return bands_in_plot

    def _add_legend(self, ax, names, colors, fontsize=10, markersize=4):
        legend_lines = []
        legend_labels = []
        for name, color in zip(names, colors):
            legend_lines.append(
                plt.Line2D(
                    [0],
                    [0],
                    marker="o",
                    markersize=markersize,
                    linestyle="",
                    color=color,
                )
            )
            legend_labels.append(f"${name}$")

        leg = ax.get_legend()

        if leg is None:
            handles = legend_lines
            labels = legend_labels
        else:
            handles = [l._legmarker for l in leg.legendHandles]
            labels = [text._text for text in leg.texts]
            handles.extend(legend_lines)
            labels.extend(legend_labels)

        ax.legend(
            handles,
            labels,
            ncol=1,
            loc="upper left",
            fontsize=fontsize,
            bbox_to_anchor=(1, 1),
            borderaxespad=0,
            frameon=False,
            handletextpad=0.1,
        )

    def _heatmap(
        self,
        ax,
        wave_vectors,
        eigenvalues,
        weights,
        sigma,
        cmap,
        bins,
        projection=None,
        powernorm=True,
        gamma=0.5,
    ):
        eigenvalues_ravel = np.ravel(eigenvalues)
        wave_vectors_tile = np.tile(wave_vectors, eigenvalues.shape[0])

        if projection is not None:
            if len(np.squeeze(projection).shape) == 2:
                weights *= np.squeeze(projection)
            else:
                weights *= np.sum(np.squeeze(projection), axis=2)

        weights_ravel = np.ravel(weights)

        data = np.histogram2d(
            wave_vectors_tile,
            eigenvalues_ravel,
            bins=bins,
            weights=weights_ravel,
        )[0]

        data = gaussian_filter(data, sigma=sigma)
        if powernorm:
            norm = colors.PowerNorm(gamma=gamma, vmin=np.min(data), vmax=np.max(data))
        else:
            norm = colors.Normalize(vmin=np.min(data), vmax=np.max(data))

        ax.pcolormesh(
            np.linspace(np.min(wave_vectors), np.max(wave_vectors), bins),
            np.linspace(np.min(eigenvalues), np.max(eigenvalues), bins),
            data.T,
            shading="gouraud",
            cmap=cmap,
            norm=norm,
        )

    def _alpha_cmap(self, color, repeats=3):
        cmap = LinearSegmentedColormap.from_list(
            "custom_cmap",
            [to_rgb(color) + (0,)] + [to_rgba(color) for _ in range(repeats)],
            N=10000,
        )
        return cmap

    def plot_plain(
        self,
        ax,
        color="black",
        erange=[-6, 6],
        linewidth=1.25,
        scale_factor=20,
        linestyle="-",
        heatmap=False,
        bins=400,
        sigma=3,
        cmap="hot",
        vlinecolor="black",
        powernorm=False,
        gamma=0.5,
        projection=None,
        highlight_band=False,
        highlight_band_color="red",
        band_index=None,
        sp_color="red",
        sp_scale_factor=5,
    ):
        """
        This function plots a plain band structure.

        Parameters:
            ax (matplotlib.pyplot.axis): Axis to plot the data on
            color (str): Color of the band structure lines
            linewidth (float): Line width of the band structure lines
            linestyle (str): Line style of the bands
        """
        bands_in_plot = self._filter_bands(erange=erange)
        slices = self._get_slices(unfold=self.unfold, hse=self.hse)
        wave_vector_segments = self._get_k_distance()

        # if self.soc_axis is not None and self.lsorbit:
        #     color = "black"
        #     linestyle = "-"

        if self.soc_axis is not None and self.lsorbit:
            if self.unfold:
                K_indices = np.array(self.K_indices[0], dtype=int)
                spin_projection_full_k = self.spin_projections[:, K_indices]
            else:
                spin_projection_full_k = self.spin_projections

        if self.custom_kpath is not None:
            kpath_inds = self.custom_kpath_inds
            kpath_flip = self.custom_kpath_flip
        else:
            kpath_inds = range(len(slices))
            kpath_flip = [False for _ in range(len(slices))]

        for i, f, wave_vectors in zip(kpath_inds, kpath_flip, wave_vector_segments):
            if f:
                eigenvalues = np.flip(
                    self.eigenvalues[bands_in_plot, slices[i]], axis=1
                )
                if self.soc_axis is not None and self.lsorbit:
                    spin_projections = np.flip(
                        spin_projection_full_k[bands_in_plot, slices[i]],
                        axis=1,
                    )
            else:
                eigenvalues = self.eigenvalues[bands_in_plot, slices[i]]
                if self.soc_axis is not None and self.lsorbit:
                    spin_projections = spin_projection_full_k[bands_in_plot, slices[i]]

            if highlight_band:
                if band_index is not None:
                    if type(band_index) == int:
                        highlight_eigenvalues = self.eigenvalues[
                            int(band_index), slices[i]
                        ]
                    else:
                        highlight_eigenvalues = self.eigenvalues[band_index, slices[i]]

            wave_vectors_for_kpoints = wave_vectors

            if self.interpolate:
                (
                    wave_vectors,
                    eigenvalues,
                ) = self._get_interpolated_data_segment(
                    wave_vectors_for_kpoints,
                    eigenvalues,
                )
                if self.soc_axis is not None and self.lsorbit:
                    _, spin_projections = self._get_interpolated_data_segment(
                        wave_vectors_for_kpoints,
                        spin_projections,
                        crop_zero=True,
                        kind="linear",
                    )

                if highlight_band:
                    if band_index is not None:
                        (
                            _,
                            highlight_eigenvalues,
                        ) = self._get_interpolated_data_segment(
                            wave_vectors_for_kpoints,
                            highlight_eigenvalues,
                        )

            eigenvalues_ravel = np.ravel(
                np.c_[eigenvalues, np.empty(eigenvalues.shape[0]) * np.nan]
            )
            wave_vectors_tile = np.tile(
                np.append(wave_vectors, np.nan), eigenvalues.shape[0]
            )

            if self.soc_axis is not None and self.lsorbit:
                #  spin_cmap = self._alpha_cmap(color=spin_projection_color, repeats=1)
                spin_projections_ravel = np.ravel(
                    np.c_[
                        spin_projections,
                        np.empty(spin_projections.shape[0]) * np.nan,
                    ]
                )
                #  spin_colors = [spin_cmap(s) for s in spin_projections_ravel]

            if self.unfold:
                spectral_weights = self.spectral_weights[bands_in_plot, slices[i]]
                if f:
                    spectral_weights = np.flip(spectral_weights, axis=1)
                #  spectral_weights = spectral_weights / np.max(spectral_weights)

                if highlight_band:
                    if band_index is not None:
                        highlight_spectral_weights = self.spectral_weights[
                            int(band_index), slices[i]
                        ]

                if self.interpolate:
                    _, spectral_weights = self._get_interpolated_data_segment(
                        wave_vectors_for_kpoints,
                        spectral_weights,
                        crop_zero=True,
                        kind="linear",
                    )

                    if highlight_band:
                        if band_index is not None:
                            (
                                _,
                                highlight_spectral_weights,
                            ) = self._get_interpolated_data_segment(
                                wave_vectors_for_kpoints,
                                highlight_spectral_weights,
                                crop_zero=True,
                                kind="linear",
                            )

                spectral_weights_ravel = np.ravel(
                    np.c_[
                        spectral_weights,
                        np.empty(spectral_weights.shape[0]) * np.nan,
                    ]
                )

                if heatmap:
                    self._heatmap(
                        ax=ax,
                        wave_vectors=wave_vectors,
                        eigenvalues=eigenvalues,
                        weights=spectral_weights,
                        sigma=sigma,
                        cmap=cmap,
                        bins=bins,
                        projection=projection,
                        powernorm=powernorm,
                        gamma=gamma,
                    )
                else:
                    ax.scatter(
                        wave_vectors_tile,
                        eigenvalues_ravel,
                        c=color,
                        ec=[(1, 1, 1, 0)],
                        s=scale_factor * spectral_weights_ravel,
                        zorder=0,
                    )
                    if highlight_band:
                        if band_index is not None:
                            if type(band_index) == int:
                                ax.scatter(
                                    wave_vectors,
                                    highlight_eigenvalues,
                                    c=highlight_band_color,
                                    ec=[(1, 1, 1, 0)],
                                    s=scale_factor * highlight_spectral_weights,
                                    zorder=100,
                                )
                            else:
                                ax.scatter(
                                    np.tile(
                                        np.append(wave_vectors, np.nan),
                                        highlight_eigenvalues.shape[0],
                                    ),
                                    np.ravel(
                                        np.c_[
                                            highlight_eigenvalues,
                                            np.empty(highlight_eigenvalues.shape[0])
                                            * np.nan,
                                        ]
                                    ),
                                    c=highlight_band_color,
                                    ec=[(1, 1, 1, 0)],
                                    s=scale_factor
                                    * np.ravel(highlight_spectral_weights),
                                    zorder=100,
                                )
                    if self.soc_axis is not None and self.lsorbit:
                        ax.scatter(
                            wave_vectors_tile,
                            eigenvalues_ravel,
                            s=spectral_weights_ravel
                            * sp_scale_factor
                            * spin_projections_ravel,
                            c=sp_color,
                            zorder=100,
                        )
            else:
                if heatmap:
                    self._heatmap(
                        ax=ax,
                        wave_vectors=wave_vectors,
                        eigenvalues=eigenvalues,
                        weights=np.ones(eigenvalues.shape),
                        sigma=sigma,
                        cmap=cmap,
                        bins=bins,
                        projection=projection,
                        powernorm=powernorm,
                        gamma=gamma,
                    )
                else:
                    ax.plot(
                        wave_vectors_tile,
                        eigenvalues_ravel,
                        color=color,
                        linewidth=linewidth,
                        linestyle=linestyle,
                        zorder=0,
                    )
                    if highlight_band:
                        if band_index is not None:
                            if type(band_index) == int:
                                ax.plot(
                                    wave_vectors,
                                    highlight_eigenvalues,
                                    color=highlight_band_color,
                                    linewidth=linewidth,
                                    linestyle=linestyle,
                                    zorder=100,
                                )
                            else:
                                ax.plot(
                                    np.tile(
                                        np.append(wave_vectors, np.nan),
                                        highlight_eigenvalues.shape[0],
                                    ),
                                    np.ravel(
                                        np.c_[
                                            highlight_eigenvalues,
                                            np.empty(highlight_eigenvalues.shape[0])
                                            * np.nan,
                                        ]
                                    ),
                                    color=highlight_band_color,
                                    linewidth=linewidth,
                                    linestyle=linestyle,
                                    zorder=100,
                                )
                    if self.soc_axis is not None and self.lsorbit:
                        ax.scatter(
                            wave_vectors_tile,
                            eigenvalues_ravel,
                            s=sp_scale_factor * spin_projections_ravel,
                            c=sp_color,
                            zorder=100,
                        )

        if self.hse:
            self._get_kticks_hse(
                ax=ax,
                wave_vectors=np.concatenate(self._get_k_distance()),
                kpath=self.kpath,
                vlinecolor=vlinecolor,
            )
        elif self.unfold:
            self._get_kticks_unfold(
                ax=ax,
                wave_vectors=np.concatenate(self._get_k_distance()),
                vlinecolor=vlinecolor,
            )
        else:
            self._get_kticks(
                ax=ax,
                wave_vectors=np.concatenate(self._get_k_distance()),
                vlinecolor=vlinecolor,
            )

        ax.set_xlim(0, np.concatenate(self._get_k_distance()).max())

    def _plot_projected_general(
        self,
        ax,
        projected_data,
        colors,
        scale_factor=5,
        erange=[-6, 6],
        display_order=None,
        linewidth=0.75,
        band_color="black",
        heatmap=False,
        bins=400,
        sigma=3,
        cmap="hot",
        vlinecolor="black",
        powernorm=False,
        gamma=0.5,
        plain_scale_factor=10,
    ):
        """
        This is a general method for plotting projected data

        Parameters:
            scale_factor (float): Factor to scale weights. This changes the size of the
                points in the scatter plot
            color_dict (dict[str][str]): This option allow the colors of each orbital
                specified. Should be in the form of:
                {'orbital index': <color>, 'orbital index': <color>, ...}
            legend (bool): Determines if the legend should be included or not.
            linewidth (float): Line width of the plain band structure plotted in the background
            band_color (string): Color of the plain band structure
        """
        if self.unfold:
            if band_color == "black":
                band_color = "darkgrey"
            scale_factor = scale_factor * 4

        bands_in_plot = self._filter_bands(erange=erange)
        slices = self._get_slices(unfold=self.unfold, hse=self.hse)

        if self.unfold:
            K_indices = np.array(self.K_indices[0], dtype=int)
            projected_data = projected_data[:, K_indices, :]

        self.plot_plain(
            ax=ax,
            linewidth=linewidth,
            color=band_color,
            erange=erange,
            heatmap=heatmap,
            sigma=sigma,
            cmap=cmap,
            bins=bins,
            vlinecolor=vlinecolor,
            projection=projected_data,
            scale_factor=plain_scale_factor,
            sp_scale_factor=0,
        )

        wave_vector_segments = self._get_k_distance()

        if self.custom_kpath is not None:
            kpath_inds = self.custom_kpath_inds
            kpath_flip = self.custom_kpath_flip
        else:
            kpath_inds = range(len(slices))
            kpath_flip = [False for _ in range(len(slices))]

        for i, f, wave_vectors in zip(kpath_inds, kpath_flip, wave_vector_segments):
            projected_data_slice = projected_data[bands_in_plot, slices[i]]
            if f:
                eigenvalues = np.flip(
                    self.eigenvalues[bands_in_plot, slices[i]], axis=1
                )
                projected_data_slice = np.flip(projected_data_slice, axis=1)
            else:
                eigenvalues = self.eigenvalues[bands_in_plot, slices[i]]

            unique_colors = np.unique(colors)
            shapes = (
                projected_data_slice.shape[0],
                projected_data_slice.shape[1],
                projected_data_slice.shape[-1],
            )
            projected_data_slice = projected_data_slice.reshape(shapes)

            if len(unique_colors) == len(colors):
                plot_colors = colors
            else:
                unique_inds = [np.isin(colors, c) for c in unique_colors]
                projected_data_slice = np.squeeze(projected_data_slice)
                projected_data_slice = np.c_[
                    [np.sum(projected_data_slice[..., u], axis=2) for u in unique_inds]
                ].transpose((1, 2, 0))
                plot_colors = unique_colors

            wave_vectors_old = wave_vectors

            if self.interpolate:
                (
                    wave_vectors,
                    eigenvalues,
                ) = self._get_interpolated_data_segment(wave_vectors_old, eigenvalues)
                _, projected_data_slice = self._get_interpolated_data_segment(
                    wave_vectors_old,
                    projected_data_slice,
                    crop_zero=True,
                    kind="linear",
                )

            if not heatmap:
                if self.unfold:
                    spectral_weights = self.spectral_weights[bands_in_plot, slices[i]]
                    if f:
                        spectral_weights = np.flip(spectral_weights, axis=1)
                    #  spectral_weights = spectral_weights / np.max(spectral_weights)

                    if self.interpolate:
                        (
                            _,
                            spectral_weights,
                        ) = self._get_interpolated_data_segment(
                            wave_vectors_old,
                            spectral_weights,
                            crop_zero=True,
                            kind="linear",
                        )

                    spectral_weights_ravel = np.repeat(
                        np.ravel(spectral_weights),
                        projected_data_slice.shape[-1],
                    )

                projected_data_ravel = np.ravel(projected_data_slice)
                wave_vectors_tile = np.tile(
                    np.repeat(wave_vectors, projected_data_slice.shape[-1]),
                    projected_data_slice.shape[0],
                )
                eigenvalues_tile = np.repeat(
                    np.ravel(eigenvalues), projected_data_slice.shape[-1]
                )
                colors_tile = np.tile(
                    plot_colors, np.prod(projected_data_slice.shape[:-1])
                )

                if display_order is None:
                    pass
                else:
                    sort_index = np.argsort(projected_data_ravel)

                    if display_order == "all":
                        sort_index = sort_index[::-1]

                    wave_vectors_tile = wave_vectors_tile[sort_index]
                    eigenvalues_tile = eigenvalues_tile[sort_index]
                    colors_tile = colors_tile[sort_index]
                    projected_data_ravel = projected_data_ravel[sort_index]

                    if self.unfold:
                        spectral_weights_ravel = spectral_weights_ravel[sort_index]

                if self.unfold:
                    s = scale_factor * projected_data_ravel * spectral_weights_ravel
                    # ec = None
                else:
                    s = scale_factor * projected_data_ravel
                    # ec = colors_tile

                ax.scatter(
                    wave_vectors_tile,
                    eigenvalues_tile,
                    c=colors_tile,
                    ec=[(1, 1, 1, 0)],
                    s=s,
                    zorder=100,
                )

    def plot_plain_old(
        self,
        ax,
        color="black",
        erange=[-6, 6],
        linewidth=1.25,
        scale_factor=20,
        linestyle="-",
        heatmap=False,
        bins=400,
        sigma=3,
        cmap="hot",
        vlinecolor="black",
        powernorm=False,
        gamma=0.5,
        projection=None,
        highlight_band=False,
        highlight_band_color="red",
        band_index=None,
    ):
        """
        This function plots a plain band structure.

        Parameters:
            ax (matplotlib.pyplot.axis): Axis to plot the data on
            color (str): Color of the band structure lines
            linewidth (float): Line width of the band structure lines
            linestyle (str): Line style of the bands
        """
        bands_in_plot = self._filter_bands(erange=erange)
        eigenvalues = self.eigenvalues[bands_in_plot]

        if highlight_band:
            if band_index is not None:
                highlight_eigenvalues = self.eigenvalues[int(band_index)]

        wave_vectors = self._get_k_distance()
        wave_vectors_for_kpoints = wave_vectors

        if self.interpolate:
            wave_vectors, eigenvalues = self._get_interpolated_data_segment(
                wave_vectors_for_kpoints, eigenvalues
            )

            if highlight_band:
                if band_index is not None:
                    (
                        _,
                        highlight_eigenvalues,
                    ) = self._get_interpolated_data_segment(
                        wave_vectors_for_kpoints,
                        highlight_eigenvalues,
                    )

        eigenvalues_ravel = np.ravel(
            np.c_[eigenvalues, np.empty(eigenvalues.shape[0]) * np.nan]
        )
        wave_vectors_tile = np.tile(
            np.append(wave_vectors, np.nan), eigenvalues.shape[0]
        )

        if self.unfold:
            spectral_weights = self.spectral_weights[bands_in_plot]
            #  spectral_weights = spectral_weights / np.max(spectral_weights)

            if highlight_band:
                if band_index is not None:
                    highlight_spectral_weights = self.spectral_weights[int(band_index)]

            if self.interpolate:
                _, spectral_weights = self._get_interpolated_data_segment(
                    wave_vectors_for_kpoints,
                    spectral_weights,
                    crop_zero=True,
                    kind="linear",
                )

                if highlight_band:
                    if band_index is not None:
                        (
                            _,
                            highlight_spectral_weights,
                        ) = self._get_interpolated_data_segment(
                            wave_vectors_for_kpoints,
                            highlight_spectral_weights,
                            crop_zero=True,
                            kind="linear",
                        )

            spectral_weights_ravel = np.ravel(
                np.c_[
                    spectral_weights,
                    np.empty(spectral_weights.shape[0]) * np.nan,
                ]
            )

            if heatmap:
                self._heatmap(
                    ax=ax,
                    wave_vectors=wave_vectors,
                    eigenvalues=eigenvalues,
                    weights=spectral_weights,
                    sigma=sigma,
                    cmap=cmap,
                    bins=bins,
                    projection=projection,
                    powernorm=powernorm,
                    gamma=gamma,
                )
            else:
                ax.scatter(
                    wave_vectors_tile,
                    eigenvalues_ravel,
                    c=color,
                    ec=[(1, 1, 1, 0)],
                    s=scale_factor * spectral_weights_ravel,
                    zorder=0,
                )
                if highlight_band:
                    if band_index is not None:
                        ax.scatter(
                            wave_vectors,
                            highlight_eigenvalues,
                            c=highlight_band_color,
                            ec=[(1, 1, 1, 0)],
                            s=scale_factor * highlight_spectral_weights,
                            zorder=100,
                        )
        else:
            if heatmap:
                self._heatmap(
                    ax=ax,
                    wave_vectors=wave_vectors,
                    eigenvalues=eigenvalues,
                    weights=np.ones(eigenvalues.shape),
                    sigma=sigma,
                    cmap=cmap,
                    bins=bins,
                    projection=projection,
                    powernorm=powernorm,
                    gamma=gamma,
                )
            else:
                ax.plot(
                    wave_vectors_tile,
                    eigenvalues_ravel,
                    color=color,
                    linewidth=linewidth,
                    linestyle=linestyle,
                    zorder=0,
                )
                if highlight_band:
                    if band_index is not None:
                        ax.plot(
                            wave_vectors,
                            highlight_eigenvalues,
                            color=highlight_band_color,
                            linewidth=linewidth,
                            linestyle=linestyle,
                            zorder=100,
                        )

        if self.hse:
            self._get_kticks_hse(
                ax=ax,
                wave_vectors=wave_vectors_for_kpoints,
                kpath=self.kpath,
                vlinecolor=vlinecolor,
            )
        elif self.unfold:
            self._get_kticks_unfold(
                ax=ax,
                wave_vectors=wave_vectors_for_kpoints,
                vlinecolor=vlinecolor,
            )
        else:
            self._get_kticks(
                ax=ax,
                wave_vectors=wave_vectors_for_kpoints,
                vlinecolor=vlinecolor,
            )

        ax.set_xlim(0, np.max(wave_vectors))

    def _plot_projected_general_old(
        self,
        ax,
        projected_data,
        colors,
        scale_factor=5,
        erange=[-6, 6],
        display_order=None,
        linewidth=0.75,
        band_color="black",
        heatmap=False,
        bins=400,
        sigma=3,
        cmap="hot",
        vlinecolor="black",
        powernorm=False,
        gamma=0.5,
        plain_scale_factor=10,
    ):
        """
        This is a general method for plotting projected data

        Parameters:
            scale_factor (float): Factor to scale weights. This changes the size of the
                points in the scatter plot
            color_dict (dict[str][str]): This option allow the colors of each orbital
                specified. Should be in the form of:
                {'orbital index': <color>, 'orbital index': <color>, ...}
            legend (bool): Determines if the legend should be included or not.
            linewidth (float): Line width of the plain band structure plotted in the background
            band_color (string): Color of the plain band structure
        """
        if self.unfold:
            if band_color == "black":
                band_color = "darkgrey"
            scale_factor = scale_factor * 4

        bands_in_plot = self._filter_bands(erange=erange)
        projected_data = projected_data[bands_in_plot]
        unique_colors = np.unique(colors)
        shapes = (
            projected_data.shape[0],
            projected_data.shape[1],
            projected_data.shape[-1],
        )
        projected_data = projected_data.reshape(shapes)

        if len(unique_colors) == len(colors):
            pass
        else:
            unique_inds = [np.isin(colors, c) for c in unique_colors]
            projected_data = np.squeeze(projected_data)
            projected_data = np.c_[
                [np.sum(projected_data[..., i], axis=2) for i in unique_inds]
            ].transpose((1, 2, 0))
            colors = unique_colors

        #  projected_data = projected_data / np.max(projected_data)
        wave_vectors = self._get_k_distance()
        wave_vectors_old = wave_vectors
        eigenvalues = self.eigenvalues[bands_in_plot]

        if self.unfold:
            K_indices = np.array(self.K_indices[0], dtype=int)
            projected_data = projected_data[:, K_indices, :]

        if self.interpolate:
            wave_vectors, eigenvalues = self._get_interpolated_data(
                wave_vectors_old, eigenvalues
            )
            _, projected_data = self._get_interpolated_data(
                wave_vectors_old,
                projected_data,
                crop_zero=True,
                kind="linear",
            )

        self.plot_plain(
            ax=ax,
            linewidth=linewidth,
            color=band_color,
            erange=erange,
            heatmap=heatmap,
            sigma=sigma,
            cmap=cmap,
            bins=bins,
            vlinecolor=vlinecolor,
            projection=projected_data,
            scale_factor=plain_scale_factor,
        )

        if not heatmap:
            if self.unfold:
                spectral_weights = self.spectral_weights[bands_in_plot]
                spectral_weights = spectral_weights / np.max(spectral_weights)

                if self.interpolate:
                    _, spectral_weights = self._get_interpolated_data(
                        wave_vectors_old,
                        spectral_weights,
                        crop_zero=True,
                        kind="linear",
                    )

                spectral_weights_ravel = np.repeat(
                    np.ravel(spectral_weights), projected_data.shape[-1]
                )

            projected_data_ravel = np.ravel(projected_data)
            wave_vectors_tile = np.tile(
                np.repeat(wave_vectors, projected_data.shape[-1]),
                projected_data.shape[0],
            )
            eigenvalues_tile = np.repeat(
                np.ravel(eigenvalues), projected_data.shape[-1]
            )
            colors_tile = np.tile(colors, np.prod(projected_data.shape[:-1]))

            if display_order is None:
                pass
            else:
                sort_index = np.argsort(projected_data_ravel)

                if display_order == "all":
                    sort_index = sort_index[::-1]

                wave_vectors_tile = wave_vectors_tile[sort_index]
                eigenvalues_tile = eigenvalues_tile[sort_index]
                colors_tile = colors_tile[sort_index]
                projected_data_ravel = projected_data_ravel[sort_index]

                if self.unfold:
                    spectral_weights_ravel = spectral_weights_ravel[sort_index]

            if self.unfold:
                s = scale_factor * projected_data_ravel * spectral_weights_ravel
                ec = None
            else:
                s = scale_factor * projected_data_ravel
                ec = colors_tile

            ax.scatter(
                wave_vectors_tile,
                eigenvalues_tile,
                c=colors_tile,
                ec=ec,
                s=s,
                zorder=100,
            )

    def plot_orbitals(
        self,
        ax,
        orbitals,
        scale_factor=5,
        erange=[-6, 6],
        display_order=None,
        color_list=None,
        legend=True,
        linewidth=0.75,
        band_color="black",
        heatmap=False,
        bins=400,
        sigma=3,
        cmap="hot",
        vlinecolor="black",
        powernorm=False,
        gamma=0.5,
    ):
        """
        This function plots the projected band structure of given orbitals summed across all atoms on a given axis.

        Parameters:
            ax (matplotlib.pyplot.axis): Axis to plot the data on
            orbitals (list): List of orbits to compare

                | 0 = s
                | 1 = py
                | 2 = pz
                | 3 = px
                | 4 = dxy
                | 5 = dyz
                | 6 = dz2
                | 7 = dxz
                | 8 = dx2-y2
                | 9 = fy3x2
                | 10 = fxyz
                | 11 = fyz2
                | 12 = fz3
                | 13 = fxz2
                | 14 = fzx3
                | 15 = fx3

            scale_factor (float): Factor to scale weights. This changes the size of the
                points in the scatter plot
            color_dict (dict[str][str]): This option allow the colors of each orbital
                specified. Should be in the form of:
                {'orbital index': <color>, 'orbital index': <color>, ...}
            legend (bool): Determines if the legend should be included or not.
            linewidth (float): Line width of the plain band structure plotted in the background
            band_color (string): Color of the plain band structure
        """

        if color_list is None:
            colors = np.array([self.color_dict[i] for i in orbitals])
        else:
            colors = color_list

        projected_data = self._sum_orbitals(orbitals=orbitals)

        self._plot_projected_general(
            ax=ax,
            projected_data=projected_data,
            colors=colors,
            scale_factor=scale_factor,
            erange=erange,
            display_order=display_order,
            linewidth=linewidth,
            band_color=band_color,
            heatmap=heatmap,
            bins=bins,
            sigma=sigma,
            cmap=cmap,
            vlinecolor=vlinecolor,
        )

        if legend:
            self._add_legend(
                ax,
                names=[self.orbital_labels[i] for i in orbitals],
                colors=colors,
            )

    def plot_spd(
        self,
        ax,
        scale_factor=5,
        orbitals="spd",
        erange=[-6, 6],
        display_order=None,
        color_list=None,
        legend=True,
        linewidth=0.75,
        band_color="black",
        heatmap=False,
        bins=400,
        sigma=3,
        cmap="hot",
        vlinecolor="black",
        powernorm=False,
        gamma=0.5,
    ):
        """
        This function plots the s, p, d projected band structure onto a given axis

        Parameters:
            ax (matplotlib.pyplot.axis): Axis to plot the data on
            scale_factor (float): Factor to scale weights. This changes the size of the
                points in the scatter plot
            order (list): This determines the order in which the points are plotted on the
                graph. This is an option because sometimes certain orbitals can be hidden
                under others because they have a larger weight. For example, if the
                weights of the d orbitals are greater than that of the s orbitals, it
                might be smart to choose ['d', 'p', 's'] as the order so the s orbitals are
                plotted over the d orbitals.
            color_dict (dict[str][str]): This option allow the colors of the s, p, and d
                orbitals to be specified. Should be in the form of:
                {'s': <s color>, 'p': <p color>, 'd': <d color>}
            legend (bool): Determines if the legend should be included or not.
            linewidth (float): Line width of the plain band structure plotted in the background
            band_color (string): Color of the plain band structure
        """
        if color_list is None:
            color_list = [
                self.color_dict[0],
                self.color_dict[1],
                self.color_dict[2],
                self.color_dict[4],
            ]
            colors = np.array([color_list[i] for i in range(len(orbitals))])
        else:
            colors = color_list

        projected_data = self._sum_spd(spd=orbitals)

        self._plot_projected_general(
            ax=ax,
            projected_data=projected_data,
            colors=colors,
            scale_factor=scale_factor,
            erange=erange,
            display_order=display_order,
            linewidth=linewidth,
            band_color=band_color,
            heatmap=heatmap,
            bins=bins,
            sigma=sigma,
            cmap=cmap,
            vlinecolor=vlinecolor,
        )

        if legend:
            self._add_legend(ax, names=[i for i in orbitals], colors=colors)

    def plot_atoms(
        self,
        ax,
        atoms,
        scale_factor=5,
        erange=[-6, 6],
        display_order=None,
        color_list=None,
        legend=True,
        linewidth=0.75,
        band_color="black",
        heatmap=False,
        bins=400,
        sigma=3,
        cmap="hot",
        vlinecolor="black",
        powernorm=False,
        gamma=0.5,
    ):
        """
        This function plots the projected band structure of given atoms summed across all orbitals on a given axis.

        Parameters:
            ax (matplotlib.pyplot.axis): Axis to plot the data on
            atoms (list): List of atoms to project onto
            scale_factor (float): Factor to scale weights. This changes the size of the
                points in the scatter plot
            color_list (list): List of colors of the same length as the atoms list
            legend (bool): Determines if the legend should be included or not.
            linewidth (float): Line width of the plain band structure plotted in the background
            band_color (string): Color of the plain band structure
        """
        if color_list is None:
            colors = np.array([self.color_dict[i] for i in range(len(atoms))])
        else:
            colors = color_list

        projected_data = self._sum_atoms(atoms=atoms)

        self._plot_projected_general(
            ax=ax,
            projected_data=projected_data,
            colors=colors,
            scale_factor=scale_factor,
            erange=erange,
            display_order=display_order,
            linewidth=linewidth,
            band_color=band_color,
            heatmap=heatmap,
            bins=bins,
            sigma=sigma,
            cmap=cmap,
            vlinecolor=vlinecolor,
        )

        if legend:
            self._add_legend(ax, names=atoms, colors=colors)

    def plot_atom_orbitals(
        self,
        ax,
        atom_orbital_dict,
        scale_factor=5,
        erange=[-6, 6],
        display_order=None,
        color_list=None,
        legend=True,
        linewidth=0.75,
        band_color="black",
        heatmap=False,
        bins=400,
        sigma=3,
        cmap="hot",
        vlinecolor="black",
        powernorm=False,
        gamma=0.5,
    ):
        """
        This function plots the projected band structure of individual orbitals on a given axis.

        Parameters:
            ax (matplotlib.pyplot.axis): Axis to plot the data on
            atom_orbital_pairs (list[list]): Selected orbitals on selected atoms to plot.
                This should take the form of [[atom index, orbital_index], ...].
                To plot the px orbital of the 1st atom and the pz orbital of the 2nd atom
                in the POSCAR file, the input would be [[0, 3], [1, 2]]
            scale_factor (float): Factor to scale weights. This changes the size of the
                points in the scatter plot
            color_list (list): List of colors of the same length as the atom_orbital_pairs
            legend (bool): Determines if the legend should be included or not.
            linewidth (float): Line width of the plain band structure plotted in the background
            band_color (string): Color of the plain band structure
        """

        atom_indices = list(atom_orbital_dict.keys())
        orbital_indices = list(atom_orbital_dict.values())
        number_orbitals = [len(i) for i in orbital_indices]
        atom_indices = np.repeat(atom_indices, number_orbitals)
        orbital_symbols_long = np.hstack(
            [[self.orbital_labels[o] for o in orb] for orb in orbital_indices]
        )
        orbital_indices_long = np.hstack(orbital_indices)
        indices = np.vstack([atom_indices, orbital_indices_long]).T

        projected_data = self.projected_eigenvalues
        projected_data = np.transpose(
            np.array([projected_data[:, :, ind[0], ind[1]] for ind in indices]),
            axes=(1, 2, 0),
        )

        if color_list is None:
            colors = np.array(
                [self.color_dict[i] for i in range(len(orbital_indices_long))]
            )
        else:
            colors = color_list

        self._plot_projected_general(
            ax=ax,
            projected_data=projected_data,
            colors=colors,
            scale_factor=scale_factor,
            erange=erange,
            display_order=display_order,
            linewidth=linewidth,
            band_color=band_color,
            heatmap=heatmap,
            bins=bins,
            sigma=sigma,
            cmap=cmap,
            vlinecolor=vlinecolor,
        )

        if legend:
            self._add_legend(
                ax,
                names=[
                    f"{i[0]}({i[1]})" for i in zip(atom_indices, orbital_symbols_long)
                ],
                colors=colors,
            )

    def plot_atom_spd(
        self,
        ax,
        atom_spd_dict,
        scale_factor=5,
        erange=[-6, 6],
        display_order=None,
        color_list=None,
        legend=True,
        linewidth=0.75,
        band_color="black",
        heatmap=False,
        bins=400,
        sigma=3,
        cmap="hot",
        vlinecolor="black",
        powernorm=False,
        gamma=0.5,
    ):
        """
        This function plots the projected band structure on the s, p, and d orbitals for each specified atom in the calculated structure.

        Parameters:
            ax (matplotlib.pyplot.axis): Axis to plot the data on
            atom_spd_dict (dict): Dictionary to determine the atom and spd orbitals to project onto
                Format: {0: 'spd', 1: 'sp', 2: 's'} where 0,1,2 are atom indicies in the POSCAR
            display_order (None or str): The available options are None, 'all', 'dominant' where None
                plots the scatter points in the order presented in the atom_spd_dict, 'all' plots the
                scatter points largest --> smallest to all points are visable, and 'dominant' plots
                the scatter points smallest --> largest so only the dominant color is visable.
            scale_factor (float): Factor to scale weights. This changes the size of the
                points in the scatter plot
            color_dict (dict[str][str]): This option allow the colors of the s, p, and d
                orbitals to be specified. Should be in the form of:
                {'s': <s color>, 'p': <p color>, 'd': <d color>}
            legend (bool): Determines if the legend should be included or not.
            linewidth (float): Line width of the plain band structure plotted in the background
            band_color (string): Color of the plain band structure
        """
        atom_indices = list(atom_spd_dict.keys())
        orbital_symbols = list(atom_spd_dict.values())
        number_orbitals = [len(i) for i in orbital_symbols]
        atom_indices = np.repeat(atom_indices, number_orbitals)
        orbital_symbols_long = np.hstack([[o for o in orb] for orb in orbital_symbols])
        orbital_indices = np.hstack(
            [[self.spd_relations[o] for o in orb] for orb in orbital_symbols]
        )
        indices = np.vstack([atom_indices, orbital_indices]).T

        projected_data = self._sum_atoms(atoms=atom_indices, spd=True)
        projected_data = np.transpose(
            np.array([projected_data[:, :, ind[0], ind[1]] for ind in indices]),
            axes=(1, 2, 0),
        )

        if color_list is None:
            colors = np.array(
                [self.color_dict[i] for i in range(len(orbital_symbols_long))]
            )
        else:
            colors = color_list

        self._plot_projected_general(
            ax=ax,
            projected_data=projected_data,
            colors=colors,
            scale_factor=scale_factor,
            erange=erange,
            display_order=display_order,
            linewidth=linewidth,
            band_color=band_color,
            heatmap=heatmap,
            bins=bins,
            sigma=sigma,
            cmap=cmap,
            vlinecolor=vlinecolor,
        )

        if legend:
            self._add_legend(
                ax,
                names=[
                    f"{i[0]}({i[1]})" for i in zip(atom_indices, orbital_symbols_long)
                ],
                colors=colors,
            )

    def plot_elements(
        self,
        ax,
        elements,
        scale_factor=5,
        erange=[-6, 6],
        display_order=None,
        color_list=None,
        legend=True,
        linewidth=0.75,
        band_color="black",
        heatmap=False,
        bins=400,
        sigma=3,
        cmap="hot",
        vlinecolor="black",
        powernorm=False,
        gamma=0.5,
    ):
        """
        This function plots the projected band structure on specified elements in the calculated structure

        Parameters:
            ax (matplotlib.pyplot.axis): Axis to plot the data on
            elements (list): List of element symbols to project onto
            scale_factor (float): Factor to scale weights. This changes the size of the
                points in the scatter plot
            color_list (list): List of colors of the same length as the elements list
            legend (bool): Determines if the legend should be included or not.
            linewidth (float): Line width of the plain band structure plotted in the background
            band_color (string): Color of the plain band structure
        """
        if color_list is None:
            colors = np.array([self.color_dict[i] for i in range(len(elements))])
        else:
            colors = color_list

        projected_data = self._sum_elements(elements=elements)

        self._plot_projected_general(
            ax=ax,
            projected_data=projected_data,
            colors=colors,
            scale_factor=scale_factor,
            erange=erange,
            display_order=display_order,
            linewidth=linewidth,
            band_color=band_color,
            heatmap=heatmap,
            bins=bins,
            sigma=sigma,
            cmap=cmap,
            vlinecolor=vlinecolor,
        )

        if legend:
            self._add_legend(ax, names=elements, colors=colors)

    def plot_element_orbitals(
        self,
        ax,
        element_orbital_dict,
        scale_factor=5,
        erange=[-6, 6],
        display_order=None,
        color_list=None,
        legend=True,
        linewidth=0.75,
        band_color="black",
        heatmap=False,
        bins=400,
        sigma=3,
        cmap="hot",
        vlinecolor="black",
        powernorm=False,
        gamma=0.5,
    ):
        """
        this function plots the projected band structure on chosen orbitals for each specified element in the calculated structure.

        Parameters:
            ax (matplotlib.pyplot.axis): axis to plot the data on
            element_orbital_pairs (list[list]): List of list in the form of
                [[element symbol, orbital index], [element symbol, orbital_index], ...]
            scale_factor (float): factor to scale weights. this changes the size of the
                points in the scatter plot
            color_list (list): List of colors of the same length as the element_orbital_pairs
            legend (bool): determines if the legend should be included or not.
            linewidth (float): line width of the plain band structure plotted in the background
            band_color (string): color of the plain band structure
        """
        element_symbols = list(element_orbital_dict.keys())
        orbital_indices = list(element_orbital_dict.values())
        number_orbitals = [len(i) for i in orbital_indices]
        element_symbols_long = np.repeat(element_symbols, number_orbitals)
        element_indices = np.repeat(range(len(element_symbols)), number_orbitals)
        orbital_symbols_long = np.hstack(
            [[self.orbital_labels[o] for o in orb] for orb in orbital_indices]
        )
        orbital_indices_long = np.hstack(orbital_indices)
        indices = np.vstack([element_indices, orbital_indices_long]).T

        projected_data = self._sum_elements(elements=element_symbols, orbitals=True)
        projected_data = np.transpose(
            np.array([projected_data[:, :, ind[0], ind[1]] for ind in indices]),
            axes=(1, 2, 0),
        )

        if color_list is None:
            colors = np.array(
                [self.color_dict[i] for i in range(len(orbital_indices_long))]
            )
        else:
            colors = color_list

        self._plot_projected_general(
            ax=ax,
            projected_data=projected_data,
            colors=colors,
            scale_factor=scale_factor,
            erange=erange,
            display_order=display_order,
            linewidth=linewidth,
            band_color=band_color,
            heatmap=heatmap,
            bins=bins,
            sigma=sigma,
            cmap=cmap,
            vlinecolor=vlinecolor,
        )

        if legend:
            self._add_legend(
                ax,
                names=[
                    f"{i[0]}({i[1]})"
                    for i in zip(element_symbols_long, orbital_symbols_long)
                ],
                colors=colors,
            )

    def plot_element_spd(
        self,
        ax,
        element_spd_dict,
        scale_factor=5,
        erange=[-6, 6],
        display_order=None,
        color_list=None,
        legend=True,
        linewidth=0.75,
        band_color="black",
        heatmap=False,
        bins=400,
        sigma=3,
        cmap="hot",
        vlinecolor="black",
        powernorm=False,
        gamma=0.5,
    ):
        """
        This function plots the projected band structure on the s, p, and d orbitals for each specified element in the calculated structure.

        Parameters:
            ax (matplotlib.pyplot.axis): Axis to plot the data on
            elements (list): List of element symbols to project onto
            order (list): This determines the order in which the points are plotted on the
                graph. This is an option because sometimes certain orbitals can be hidden
                under other orbitals because they have a larger weight. For example, if the
                signitures of the d orbitals are greater than that of the s orbitals, it
                might be smart to choose ['d', 'p', 's'] as the order so the s orbitals are
                plotted over the d orbitals.
            scale_factor (float): Factor to scale weights. This changes the size of the
                points in the scatter plot
            color_dict (dict[str][str]): This option allow the colors of the s, p, and d
                orbitals to be specified. Should be in the form of:
                {'s': <s color>, 'p': <p color>, 'd': <d color>}
            legend (bool): Determines if the legend should be included or not.
            linewidth (float):12 Line width of the plain band structure plotted in the background
            band_color (string): Color of the plain band structure
        """
        element_symbols = list(element_spd_dict.keys())
        orbital_symbols = list(element_spd_dict.values())
        number_orbitals = [len(i) for i in orbital_symbols]
        element_symbols_long = np.repeat(element_symbols, number_orbitals)
        element_indices = np.repeat(range(len(element_symbols)), number_orbitals)
        orbital_symbols_long = np.hstack([[o for o in orb] for orb in orbital_symbols])
        orbital_indices = np.hstack(
            [[self.spd_relations[o] for o in orb] for orb in orbital_symbols]
        )
        indices = np.vstack([element_indices, orbital_indices]).T

        projected_data = self._sum_elements(elements=element_symbols, spd=True)
        projected_data = np.transpose(
            np.array([projected_data[:, :, ind[0], ind[1]] for ind in indices]),
            axes=(1, 2, 0),
        )

        if color_list is None:
            colors = np.array(
                [self.color_dict[i] for i in range(len(orbital_symbols_long))]
            )
        else:
            colors = color_list

        self._plot_projected_general(
            ax=ax,
            projected_data=projected_data,
            colors=colors,
            scale_factor=scale_factor,
            erange=erange,
            display_order=display_order,
            linewidth=linewidth,
            band_color=band_color,
            heatmap=heatmap,
            bins=bins,
            sigma=sigma,
            cmap=cmap,
            vlinecolor=vlinecolor,
        )

        if legend:
            self._add_legend(
                ax,
                names=[
                    f"{i[0]}({i[1]})"
                    for i in zip(element_symbols_long, orbital_symbols_long)
                ],
                colors=colors,
            )


if __name__ == "__main__":
    #  M = [
    #  [0,1,-1],
    #  [1,-1,0],
    #  [-14,-14,-14]
    #  ]
    #
    #  high_symm_points = [
    #  [2/3, 1/3, 1/3],
    #  [0.0, 0.0, 0],
    #  [2/3, 1/3, 1/3],
    #  ]
    #
    #  high_symm_points = [
    #  [0.1, 0.1, 0],
    #  [0.0, 0.0, 0],
    #  [0.1, 0.1, 0],
    #  ]

    #  band = Band(
    #  folder="../../vaspvis_data/bandAGA",
    #  projected=True,
    #  interpolate=False,
    #  unfold=True,
    #  M=M,
    #  high_symm_points=high_symm_points,
    #  n=40,
    #  kpath=[['A', 'G'], ['G', 'A']],
    #  custom_kpath=[1,2,2,-1],
    #  )
    #  fig, ax = plt.subplots(figsize=(4,3), dpi=400)
    #  band.plot_spd(ax=ax, scale_factor=100)
    #  ax.set_ylim(-2,2)
    #  fig.tight_layout()
    #  fig.savefig('test.png')

    M = [[-0.0, 0.0, -1.0], [1.0, -1.0, 0.0], [-16.0, -16.0, 16.0]]

    high_symm_points = [
        [0.5, 0.0, 0.5],
        [0.0, 0.0, 0.0],
        [0.5, 0.0, 0.5],
    ]

    band_up = Band(
        #  folder="../../vaspvis_data/band_EuS_bulk/band",
        folder="../../vaspvis_data/band_EuS_slab/band",
        interpolate=False,
        soc_axis="z",
        spin="up",
        unfold=True,
        M=M,
        high_symm_points=high_symm_points,
        n=30,
        new_n=50,
        kpath=[["X", "G"], ["G", "X"]],
    )
    band_down = Band(
        #  folder="../../vaspvis_data/band_EuS_bulk/band",
        folder="../../vaspvis_data/band_EuS_slab/band",
        interpolate=False,
        soc_axis="z",
        spin="down",
        unfold=True,
        M=M,
        high_symm_points=high_symm_points,
        n=30,
        new_n=50,
        kpath=[["X", "G"], ["G", "X"]],
    )
    fig, ax = plt.subplots(figsize=(2.5, 4), dpi=400)
    band_up.plot_plain(
        ax=ax,
        sp_color="red",
        erange=[-6, 3],
        sp_scale_factor=1,
        scale_factor=4,
    )
    band_down.plot_plain(
        ax=ax,
        sp_color="blue",
        erange=[-6, 3],
        sp_scale_factor=1,
        scale_factor=4,
    )
    ax.set_ylim(-6, 3)
    fig.tight_layout()
    fig.savefig("unfold_test.png")

    fig1, ax1 = plt.subplots(figsize=(2.5, 4), dpi=400)
    band_hse = Band(
        folder="../../vaspvis_data/band_InAs_hse",
        interpolate=False,
        projected=True,
        custom_kpath=[-1, 1],
    )
    band_hse.plot_spd(
        ax=ax1,
    )
    ax1.set_ylim([-3, 3])
    fig1.tight_layout(pad=0.4)
    fig1.savefig("hse.png")
    #  fig.savefig('bulk_test.png')
