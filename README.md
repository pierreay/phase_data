# About

Main repository of the PhaseSCA project.

This repository contains the data and experiments of the project.

## Publication

This project led to the following [TCHES](https://tches.iacr.org/) publication: *Pierre Ayoub, Aurélien Hernandez, Romain Cayre, Aurélien Francillon, Clémentine Maurice (2025), PhaseSCA: Exploiting Phase-Modulated Emanations in Side Channels. IACR Transactions on Cryptographic Hardware and Embedded Systems (TCHES'25). https://tches.iacr.org/index.php/TCHES/article/view/11934 https://hal.science/hal-04726109*

This project is also part of my Ph.D. Thesis, not yet published.

## How to cite

<!---biblio-info@06cb76e-->
``` bibtex
@Article{ayoub25phasesca,
  title         = {{PhaseSCA: Exploiting Phase-Modulated Emanations in Side
                  Channels}},
  author        = {Ayoub, Pierre and Hernandez, Aur{\'e}lien and Cayre,
                  Romain and Francillon, Aur{\'e}lien and Maurice,
                  Cl{\'e}mentine},
  journal       = {IACR Transactions on Cryptographic Hardware and Embedded
                  Systems (TCHES)},
  publisher     = {{IACR}},
  volume        = {2025},
  doi           = {10.46586/tches.v2025.i1.392-419},
  number        = {1},
  pages         = {392–419},
  year          = {2024},
  month         = {Dec.},
  url           = {https://tches.iacr.org/index.php/TCHES/article/view/11934},
  urlfile       = {https://tches.iacr.org/index.php/TCHES/article/view/11934/11793},
  keywords      = {Side-channel attacks ; Power/Electromagnetic analysis ;
                  Unintended modulation ; Phase modulation ; Angle modulation
                  ; Clock jitter},
  hal_local_reference={Rapport LAAS n{\textdegree} 25001},
  hal_url       = {https://hal.science/hal-04726109},
  hal_pdf       = {https://hal.science/hal-04726109v1/file/paper.pdf},
  hal_id        = {hal-04726109},
  hal_version   = {v1},
  affiliations  = {Eurecom, Univ Lille, CNRS, Inria}
}
```

# Reproducing

As an introduction, one may want to check the [TCHES artifact evaluation](./docs/2025-01-23_tches-artifact/) to reproduce parts of our results.
All results and plots of the paper have been generated using one of the script and data in this repository, in such a way that everything must be easy to reproduce given the same hardware and software. 

## Requirements

### Software

- **[phase_fw](https://github.com/pierreay/phase_fw.git)**

Source code used to flash the firmware we used on our target devices during our attacks.
Required to perform the data collection.

- **[SoapyRX](https://github.com/pierreay/soapyrx.git)** 

Utility used to record I/Q signals from the target device emanations using a software-defined radio (SDR).
Required to perform the data collection.

- **[SCAFF](https://github.com/pierreay/scaff.git)**

Used to to process and analyze the datasets composed of I/Q signals.
Required to perform the data analysis and the attacks.

### Hardware

- **SDR**: Pick one supported by SoapyRX.
- **Near-field probes**: Can be a COTS one or a handmade for frequencies between 8 and 128 MHz.
- **Device Under Test (DUT)**: Pick one described in the paper.

# Storage

This repository is managed using [git-annex](https://git-annex.branchable.com/).
Around 400 GB of data is currently deduplicated and spread across 4 instances, including workstations and external mass storage devices.

# Files

- **[docs](./docs)**

Documentation of the project.

- **[expe](./expe)**

Experiments of the project.

- **[sets](./sets)**

Datasets built during the project.

- **[src](./src)**

Scripts to work with the repository.
